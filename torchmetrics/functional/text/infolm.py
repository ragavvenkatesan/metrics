import os
from enum import unique
from typing import Dict, List, Optional, Sequence, Tuple, Union

import torch
from torch import Tensor
from torch.nn import functional as F
from torch.utils.data import DataLoader
from typing_extensions import Literal

from torchmetrics.functional.text.helper_embedding_metric import (
    TokenizedDataset,
    _get_progress_bar,
    _input_data_collator,
    _load_tokenizer_and_model,
)
from torchmetrics.utilities.enums import EnumStr
from torchmetrics.utilities.imports import _TRANSFORMERS_AVAILABLE

if _TRANSFORMERS_AVAILABLE:
    from transformers import PreTrainedModel, PreTrainedTokenizerBase
else:
    __doctest_skip__ = ["infolm"]


_ALLOWED_INFORMATION_MEASURE = (
    "kl_divergence",
    "alpha_divergence",
    "beta_divergence",
    "ab_divergence" "renyi_divergence",
    "l1_distance",
    "l2_distance",
    "l_infinity_distance",
    "fisher_rao_distance",
)


_ALLOWED_INFORMATION_MEASURE_LITERAL = Literal[
    "kl_divergence",
    "alpha_divergence",
    "beta_divergence",
    "ab_divergence" "renyi_divergence",
    "l1_distance",
    "l2_distance",
    "l_infinity_distance",
    "fisher_rao_distance",
]


@unique
class _IMEnum(EnumStr):
    """A helper Enum class for storing the information measure."""

    KL_DIVERGENCE = "kl_divergence"
    ALPHA_DIVERGENCE = "alpha_divergence"
    BETA_DIVERGENCE = "beta_divergence"
    AB_DIVERGENCE = "ab_divergence"
    RENYI_DIVERGENCE = "renyi_divergence"
    L1_DISTANCE = "l1_distance"
    L2_DISTANCE = "l2_distance"
    L_INFINITY_DISTANCE = "l_infinity_distance"
    FISHER_RAO_DISTANCE = "fisher_rao_distance"

    @classmethod
    def from_str(cls, value: str) -> Optional["EnumStr"]:
        """
        Raises:
            ValueError:
                If required information measure is not among the supported options.
        """
        statuses = [status for status in dir(cls) if not status.startswith("_")]
        for st in statuses:
            if st.lower() == value.lower():
                return getattr(cls, st)
        raise ValueError(f"Invalid information measure got. Please use one of {_ALLOWED_INFORMATION_MEASURE}.")


class _InformationMeasure:
    """A wrapper class used for the calculation the result of information measure between the discrete reference
    distributions of predicted and reference sentences. The class also handles input validation for `alpha` and
    `beta` parameters.

    Args:
        information_measure:
            A name of information measure to be used. Please use one of: ['kl_divergence', 'alpha_divergence',
            'beta_divergence', 'ab_divergence', 'renyi_divergence', 'l1_distance', 'l2_distance', 'l_infinity_distance',
            'fisher_rao_distance']
        alpha:
            Alpha parameter of the divergence used for alpha, AB and Rényi divergence measures.
        beta:
            Beta parameter of the divergence used for beta and AB divergence measures.

    Raises:
        ValueError:
            If information measure is one from alpha, AB or Rényi divergence and parameter `alpha` is `None`.
        ValueError:
            If information measure is one from beta or divergence and parameter `beta` is `None`.
        ValueError:
            If information measure is alpha divergence and parameter `alpha` equals 0 or 1.
        ValueError:
            If information measure is beta divergence and parameter `beta` equals 0 or -
        ValueError:
            If information measure is AB divergence and parameter `alpha`, `beta` or `alpha + beta` equal 0.
        ValueError:
            If information measure is Rényi divergence and parameter `alpha` equals 1.
    """

    def __init__(
        self,
        information_measure: _ALLOWED_INFORMATION_MEASURE_LITERAL,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
    ) -> None:
        self.information_measure = _IMEnum.from_str(information_measure)
        if self.information_measure in [_IMEnum.ALPHA_DIVERGENCE, _IMEnum.AB_DIVERGENCE, _IMEnum.RENYI_DIVERGENCE]:
            if not isinstance(alpha, float):
                raise ValueError(f"Parameter `alpha` is expected to be defined for {information_measure}.")
        if self.information_measure in [_IMEnum.BETA_DIVERGENCE, _IMEnum.AB_DIVERGENCE]:
            if not isinstance(beta, float):
                raise ValueError(f"Parameter `beta` is expected to be defined for {information_measure}.")
        if self.information_measure == _IMEnum.ALPHA_DIVERGENCE and alpha in [0, 1]:
            raise ValueError(f"Parameter `alpha` is expected to be differened from 0 and 1 for {information_measure}.")
        if self.information_measure == _IMEnum.BETA_DIVERGENCE and alpha in [0, -1]:
            raise ValueError(f"Parameter `beta` is expected to be differened from 0 and -1 for {information_measure}.")
        if self.information_measure == _IMEnum.AB_DIVERGENCE and 0 in [alpha, beta, alpha + beta]:
            raise ValueError(
                "Parameters `alpha`, `beta` and their sum are expected to be differened from 0 for "
                f"{information_measure}."
            )
        if self.information_measure == _IMEnum.RENYI_DIVERGENCE and alpha == 1:
            raise ValueError(f"Parameter `alpha` is expected to be differened from 1 for {information_measure}.")

        self.alpha = alpha
        self.beta = beta

    def __call__(self, preds_distribution: Tensor, target_distribtuion: Tensor) -> Tensor:
        information_measure_function = getattr(self, f"_calculate_{self.information_measure}")
        return information_measure_function(preds_distribution, target_distribtuion)

    @staticmethod
    def _calculate_kl_divergence(preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate Kullback-Leibler divergence between discrete distributions of predicted and reference
        sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            Kullback-Leibler divergence between discrete distributions of predicted and reference sentences.
        """
        return torch.sum(preds_distribution * torch.log(preds_distribution / target_distribution), dim=-1)

    def _calculate_alpha_divergence(self, preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate alpha divergence between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            Alpha divergence between discrete distributions of predicted and reference sentences.
        """
        _alpha_denom = self.alpha * (self.alpha - 1)
        alpha_divergence = (
            1 - torch.sum(target_distribution ** self.alpha * preds_distribution ** (1 - self.alpha), dim=-1)
        ) / _alpha_denom
        return alpha_divergence

    def _calculate_ab_divergence(self, preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate AB divergence between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            AB divergence between discrete distributions of predicted and reference sentences.
        """
        x = torch.log(torch.sum(target_distribution ** (self.beta + self.alpha), dim=-1))
        y = torch.log(torch.sum(preds_distribution ** (self.beta + self.alpha), dim=-1))
        z = torch.log(torch.sum(target_distribution ** self.alpha * preds_distribution ** self.beta, dim=-1))
        ab_divergence = (
            x / (self.beta * (self.beta + self.alpha)) + y / (self.beta + self.alpha) - z / (self.alpha * self.beta)
        )
        return ab_divergence

    def _calculate_beta_divergence(self, preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate beta divergence between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            Beta divergence between discrete distributions of predicted and reference sentences.
        """
        self.alpha = 1.0
        beta_divergence = self._calculate_ab_divergence(preds_distribution, target_distribution)
        return beta_divergence

    def _calculate_renyi_divergence(self, preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate Rényi divergence between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            Rényi divergence between discrete distributions of predicted and reference sentences.
        """
        renyi_divergence = (
            torch.log(torch.sum(target_distribution ** self.alpha * preds_distribution ** (1 - self.alpha), dim=-1))
        ) / (self.alpha - 1)
        return renyi_divergence

    @staticmethod
    def _calculate_l1_distance(preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate L1 distance between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            L1 distance between discrete distributions of predicted and reference sentences.
        """
        return torch.norm(target_distribution - preds_distribution, p=1, dim=-1)

    @staticmethod
    def _calculate_l2_distance(preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate L2 distance between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            L2 distance between discrete distributions of predicted and reference sentences.
        """
        return torch.norm(target_distribution - preds_distribution, p=2, dim=-1)

    @staticmethod
    def _calculate_l_infinity_distance(preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate L-infinity distance between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            L-infinity distance between discrete distributions of predicted and reference sentences.
        """
        return torch.norm(target_distribution - preds_distribution, p=float("inf"), dim=-1)

    @staticmethod
    def _calculate_fisher_rao_distance(preds_distribution: Tensor, target_distribution: Tensor) -> Tensor:
        """Calculate Fisher-Rao distance between discrete distributions of predicted and reference sentences.

        Args:
            preds_distribution:
                Discrete reference distribution of predicted sentences over the vocabulary.
            target_distribution:
                Discrete reference distribution of reference sentences over the vocabulary.

        Return:
            Fisher-Rao distance between discrete distributions of predicted and reference sentences.
        """
        return 2 * torch.acos(torch.clamp(torch.sqrt(preds_distribution * target_distribution).sum(-1), 0, 1))


def _get_dataloader(
    input_ids: Tensor, attention_mask: Tensor, idf: bool, batch_size: int, num_workers: int
) -> DataLoader:
    """Prepare dataloader.

    Args:
        input_ids:
            Indices of input sequence tokens in the vocabulary.
        attention_mask:
            Mask to avoid performing attention on padding token indices.
        idf:
            A batch size used for model processing.
        num_threads:
            A number of workers to use for a dataloader.

    Return:
        An instance of ``torch.utils.data.DataLoader`` used for iterating over examples.
    """
    dataset = TokenizedDataset(input_ids, attention_mask, idf)
    dataloader = DataLoader(dataset, batch_size=batch_size, num_workers=num_workers)
    return dataloader


def _get_special_tokens_map(tokenizer: PreTrainedModel) -> Dict[str, int]:
    """Build a dictionary of model/tokenizer special tokens.

    Args:
        tokenizer:
            Initialized tokenizer from HuggingFace's `transformers package.

    Return:
        A dictionary containing: mask_token_id, pad_token_id, sep_token_id and cls_token_id.
    """
    special_tokens_maps = {
        "mask_token_id": tokenizer.mask_token_id,
        "pad_token_id": tokenizer.pad_token_id,
        "sep_token_id": tokenizer.sep_token_id,
        "cls_token_id": tokenizer.cls_token_id,
    }
    return special_tokens_maps


def _get_token_mask(input_ids: Tensor, pad_token_id: int, sep_token_id: int, cls_token_id: int) -> Tensor:
    """Generate a token mask for differentiating all special tokens in the input batch.

    Args:
        input_ids:
            Indices of input sequence tokens in the vocabulary.
        pad_token_id:
            An id of ``<PAD>`` tokens that are used to make arrays of tokens the same size for batching purpose
        cls_token_id:
            An id of ``<CLS>`` token that represents the class of the input. (It might be ``<BOS>`` token for some
            models.)
        sep_token_id:
            An id of ``<SEP>`` token that separates two different sentences in the same input. (It might be ``<EOS>``
            token for some models.)

    Return:
        Tensor mask of 0s and 1s that masks all special tokens in the ``input_ids`` tensor.
    """
    token_mask = input_ids.eq(pad_token_id) | input_ids.eq(sep_token_id) | input_ids.eq(cls_token_id)
    return token_mask


def _get_batch_distribution(
    model: PreTrainedModel, batch: Dict[str, Tensor], temperature: float, idf: bool, special_tokens_map: Dict[str, int]
) -> Tensor:
    """Calculate a discrete probability distribution for a batch of examples according to the methodology described
    in `InfoLM`_.

    Args:
        model:
            Initialized model from HuggingFace's `transformers package.
        batch:
            An input batch dictionary containing ``input_ids`` and ``attention_mask``.
        temperature:
            A temperature for calibrating language modelling. For more information, please reference `InfoLM`_ paper.
        max_length:
            A maximum length of input sequences. Sequences longer than `max_length` are to be trimmed.
        idf:
            An indication of whether normalization using inverse document frequencies should be used.

    Return:
        A discrete probability distribution.
    """
    seq_len = batch["input_ids"].shape[1]
    prob_distribution_batch: Union[Tensor, List[Tensor]] = []
    token_mask = _get_token_mask(
        batch["input_ids"],
        special_tokens_map["pad_token_id"],
        special_tokens_map["sep_token_id"],
        special_tokens_map["cls_token_id"],
    )

    for mask_idx in range(seq_len):
        input_ids = batch["input_ids"].clone()
        input_ids[:, mask_idx] = special_tokens_map["mask_token_id"]

        logits_distribution = model(input_ids, batch["attention_mask"]).logits
        logits_distribution = logits_distribution[
            :, mask_idx, :
        ]  # [batch_size, seq_len, vocab_size] -> [batch_size, vocab_size]
        prob_distribution = F.softmax(logits_distribution / temperature, dim=-1)
        prob_distribution_batch.append(prob_distribution.unsqueeze(1).cpu())  # [batch_size, 1, vocab_size]
        # Clean from memory
        del input_ids, logits_distribution, prob_distribution

    prob_distribution_batch = torch.cat(prob_distribution_batch, dim=1)  # [batch_size, seq_len, vocab_size]
    prob_distribution_batch = torch.einsum("bsv, bs -> bsv", prob_distribution_batch, token_mask)
    prob_distribution_batch = prob_distribution_batch.sum(dim=1) / token_mask.sum(dim=1).unsqueeze(1)

    return prob_distribution_batch


@torch.no_grad()
def _get_data_distribution(
    model: PreTrainedModel,
    dataloader: DataLoader,
    temperature: float,
    idf: bool,
    special_tokens_map: Dict[str, int],
    verbose: bool,
) -> Tensor:
    """Calculate a discrete probability distribution according to the methodology described in `InfoLM`_.
    Args:
        model:
            Initialized model from HuggingFace's `transformers package.
        dataloader:
            An instance of `torch.utils.data.DataLoader` used for iterating over examples.
        temperature:
            A temperature for calibrating language modelling. For more information, please reference `InfoLM`_ paper.
        max_length:
            A maximum length of input sequences. Sequences longer than `max_length` are to be trimmed.
        idf:
            An indication of whether normalization using inverse document frequencies should be used.
        verbose:
            An indication of whether a progress bar to be displayed during the embeddings calculation.

    Return:
        A discrete probability distribution.
    """
    device = model.device
    prob_distribution: List[Tensor] = []

    for batch in _get_progress_bar(dataloader, verbose):
        batch = _input_data_collator(batch, device)
        prob_distribution.append(_get_batch_distribution(model, batch, temperature, idf, special_tokens_map))

    return torch.cat(prob_distribution, dim=0)


def _infolm_update(
    preds: Union[str, Sequence[str]],
    target: Union[str, Sequence[str]],
    tokenizer: PreTrainedTokenizerBase,
    max_length: int,
) -> Tuple[Tensor, Tensor, Tensor, Tensor]:
    """Update the metric state by a tokenization of ``preds`` and ``target`` sentencens.

    Args:
        preds:
            An iterable of hypothesis corpus.
        target:
            An iterable of reference corpus.
        tokenizer:
            Initialized tokenizer from HuggingFace's `transformers package.
        max_length:
            A maximum length of input sequences. Sequences longer than `max_length` are to be trimmed.

    Return:
        Tokenizerd ``preds`` and ``target`` sentences represented with ``input_ids`` and ``attention_mask`` tensors.
    """
    preds_input = tokenizer(preds, padding="max_length", max_length=max_length, truncation=True, return_tensors="pt")
    target_input = tokenizer(target, padding="max_length", max_length=max_length, truncation=True, return_tensors="pt")

    return preds_input.input_ids, preds_input.attention_mask, target_input.input_ids, target_input.attention_mask


def _infolm_compute(
    model: PreTrainedModel,
    preds_dataloader: DataLoader,
    target_dataloader: DataLoader,
    temperature: float,
    idf: bool,
    information_measure_cls: _InformationMeasure,
    special_tokens_map: Dict[str, int],
    verbose: bool = True,
):
    """Calculate selected information measure using the pre-trained language model.

    Args:
        model:
            Initialized model from HuggingFace's `transformers package.
        preds_dataloader:
            Loader iterating over tokenizer predicted sentences.
        target_datalaoder:
            Loader iterating over tokenizer reference sentences.
        temperature:
            A temperature for calibrating language modelling. For more information, please reference `InfoLM`_ paper.
        idf:
            An indication of whether normalization using inverse document frequencies should be used.
        information_measure_cls:
            Information measure class containing all parameters necessary for calculating information measure values
            using ``preds_distribution`` and ``target_distribution``.
        special_tokens_map:
            A dictionary mapping tokenizer special tokens into the corresponding integer values.
        verbose:
            An indication of whether a progress bar to be displayed during the embeddings calculation.

    Return:
        A corpus-level InfoLM score.
    """
    preds_distribution = _get_data_distribution(model, preds_dataloader, temperature, idf, special_tokens_map, verbose)
    target_distribution = _get_data_distribution(
        model, target_dataloader, temperature, idf, special_tokens_map, verbose
    )
    infolm_score = information_measure_cls(preds_distribution, target_distribution)
    return infolm_score


def infolm(
    preds: Union[str, Sequence[str]],
    target: Union[str, Sequence[str]],
    model_name_or_path: Union[str, os.PathLike] = "bert-base-uncased",
    temperature: float = 0.25,
    information_measure: _ALLOWED_INFORMATION_MEASURE_LITERAL = "kl_divergence",
    idf: bool = True,
    alpha: Optional[float] = None,
    beta: Optional[float] = None,
    device: Optional[Union[str, torch.device]] = None,
    max_length: Optional[int] = None,
    batch_size: int = 64,
    num_threads: int = 4,
    verbose: bool = True,
    return_sentence_level_score: bool = False,
) -> Tensor:
    """
    Calculate `InfoLM`_ [1] - i.e. calculate a distance/divergence between predicted and reference sentence discrete
    distribution using one of the following information measures:
        - `KL divergence`_
        - `alpha divergence`_
        - `beta divergence`_
        - `AB divergence`_
        - `Rényi divergence`_
        - L1 distance
        - L2 distance
        - L-infinity distance
        - `Fisher-Rao distance`_

    `InfoLM`_ is a family of untrained embedding-based metrics which addresses some famous flaws of standard
    string-based metrics thanks to the usage of pre-trained masked language models. This family of metrics is mainly
    designed for summarization and data-to-text tasks.

    The implementation of this metric is fully based HuggingFace `transformers`' package.

    Args:
        preds:
            An iterable of hypothesis corpus.
        target:
            An iterable of reference corpus.
        model_name_or_path:
            A name or a model path used to load `transformers` pretrained model.
        temperature:
            A temperature for calibrating language modelling. For more information, please reference `InfoLM`_ paper.
        information_measure:
            A name of information measure to be used. Please use one of: ['kl_divergence', 'alpha_divergence',
            'beta_divergence', 'ab_divergence', 'renyi_divergence', 'l1_distance', 'l2_distance', 'l_infinity_distance',
            'fisher_rao_distance']
        idf:
            An indication of whether normalization using inverse document frequencies should be used.
        alpha:
            Alpha parameter of the divergence used for alpha, AB and Rényi divergence measures.
        beta:
            Beta parameter of the divergence used for beta and AB divergence measures.
        device:
            A device to be used for calculation.
        max_length:
            A maximum length of input sequences. Sequences longer than `max_length` are to be trimmed.
        batch_size:
            A batch size used for model processing.
        num_threads:
            A number of threads to use for a dataloader.
        verbose:
            An indication of whether a progress bar to be displayed during the embeddings calculation.
        return_sentence_level_score:
            An indication whether a sentence-level chrF/chrF++ score to be returned.

    Returns:
        A corpus-level InfoLM score.
        (Optionally) A list of sentence-level InfoLM scores if `return_sentence_level_score=True`.

    Example:
        >>> from torchmetrics.functional.text.infolm import infolm
        >>> preds = ['the cat is on the mat']
        >>> target = ['there is a cat on the mat']
        >>> infolm(preds, target)

    References:
        [1] InfoLM: A New Metric to Evaluate Summarization & Data2Text Generation by Pierre Colombo, Chloé Clavel and
        Pablo Piantanida `InfoLM`_
    """
    tokenizer, model = _load_tokenizer_and_model(model_name_or_path, device)
    information_measure_cls = _InformationMeasure(information_measure, alpha, beta)
    max_length = max_length or model.config.max_length
    special_tokens_map = _get_special_tokens_map(tokenizer)

    preds_input_ids, preds_attention_mask, target_input_ids, target_attention_mask = _infolm_update(
        preds, target, tokenizer, max_length
    )
    preds_dataloader = _get_dataloader(preds_input_ids, preds_attention_mask, idf, batch_size, num_threads)
    target_dataloader = _get_dataloader(target_input_ids, target_attention_mask, idf, batch_size, num_threads)

    info_lm_score = _infolm_compute(
        model,
        preds_dataloader,
        target_dataloader,
        temperature,
        idf,
        information_measure_cls,
        special_tokens_map,
        verbose,
    )

    return info_lm_score