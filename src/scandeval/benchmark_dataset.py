"""Abstract benchmarking dataset class."""

import itertools as it
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import evaluate
import numpy as np
import pandas as pd
from datasets.arrow_dataset import Dataset
from datasets.dataset_dict import DatasetDict
from datasets.load import load_dataset
from huggingface_hub.utils._errors import HfHubHTTPError
from tqdm.auto import tqdm
from transformers import PretrainedConfig
from transformers.modeling_utils import PreTrainedModel

from .config import BenchmarkConfig, DatasetConfig, ModelConfig
from .dataset_tasks import SPEED
from .exceptions import InvalidBenchmark
from .finetuning import finetune
from .generation import generate
from .model_config import get_model_config
from .model_loading import load_model
from .model_setups import GenerativeModel, Tokenizer
from .scores import log_scores
from .speed_benchmark import benchmark_speed
from .types import SCORE_DICT
from .utils import enforce_reproducibility, model_is_generative

# Set up logger
logger = logging.getLogger(__name__)


class BenchmarkDataset(ABC):
    """Abstract benchmarking dataset class.

    Args:
        dataset_config (DatasetConfig):
            The configuration of the dataset.
        benchmark_config (BenchmarkConfig):
            The configuration of the benchmark.

    Attributes:
        dataset_config (DatasetConfig):
            The configuration of the dataset.
        benchmark_config (BenchmarkConfig):
            The configuration of the benchmark.
    """

    def __init__(
        self, dataset_config: DatasetConfig, benchmark_config: BenchmarkConfig
    ) -> None:
        """Initialise the dataset.

        Args:
            dataset_config (DatasetConfig):
                The configuration for the dataset.
            benchmark_config (BenchmarkConfig):
                The configuration for the benchmark.
        """
        self.dataset_config = dataset_config
        self.benchmark_config = benchmark_config
        self._metrics = {
            metric_cfg.name: evaluate.load(
                path=metric_cfg.huggingface_id,
                cache_dir=self.benchmark_config.cache_dir,
            )
            if metric_cfg.huggingface_id != ""
            else None
            for metric_cfg in dataset_config.task.metrics
        }

        # Set logging level based on verbosity
        logging_level = logging.DEBUG if self.benchmark_config.verbose else logging.INFO
        logger.setLevel(logging_level)

    def benchmark(self, model_id: str) -> tuple[SCORE_DICT, dict[str, int]]:
        """Benchmark a model.

        Args:
            model_id (str):
                The full Hugging Face Hub path to the pretrained transformer model. The
                specific model version to use can be added after the suffix '@':
                "model_id@v1.0.0". It can be a branch name, a tag name, or a commit id,
                and defaults to the latest version if not specified.

        Returns:
            pair of dicts:
                A pair (scores, metadata_dict), with `scores` being a dictionary
                containing the scores, and `metadata_dict` being a dictionary
                containing various model metadata, such as the number of model
                parameters, the model's maximum sequence length and the size of the
                model's vocabulary. The keys in `score_dict` are 'raw' and 'total',
                with all the raw scores in the first dictionary and the aggregated
                scores in the second.

        Raises:
            RuntimeError:
                If the extracted framework is not recognized.
        """
        model_config = get_model_config(
            model_id=model_id, benchmark_config=self.benchmark_config
        )

        rng = enforce_reproducibility(framework=model_config.framework)

        tokenizer, model = load_model(
            model_config=model_config,
            dataset_config=self.dataset_config,
            benchmark_config=self.benchmark_config,
        )

        # This happens when a local model is used, as we cannot fetch the model metadata
        if model_config.task == "unknown":
            if model_is_generative(model=model):
                model_config.task = "text-generation"
            else:
                model_config.task = "fill-mask"

        metadata_dict = self._get_metadata(model=model, tokenizer=tokenizer)

        # Set variable with number of iterations
        num_iter = 10 if not self.benchmark_config.testing else 5

        if self.dataset_config.task.name != SPEED:
            train, val, tests = self._load_data(num_iter=num_iter, rng=rng)
            prepared_train, prepared_val, prepared_tests = self._load_prepared_data(
                train=train,
                val=val,
                tests=tests,
                model_config=model_config,
                hf_model_config=model.config,
                tokenizer=tokenizer,
            )

        # Set up progress bar
        itr = tqdm(
            iterable=range(num_iter),
            desc="Benchmarking",
            disable=not self.benchmark_config.progress_bar,
        )

        if self.dataset_config.task.name == SPEED:
            scores = benchmark_speed(
                itr=itr,
                tokenizer=tokenizer,
                model=model,
                model_config=model_config,
                dataset_config=self.dataset_config,
                benchmark_config=self.benchmark_config,
            )
        elif model_is_generative(model=model):
            scores = self._generate(
                itr=itr,
                train=train,
                val=val,
                tests=tests,
                prepared_train=prepared_train,
                prepared_val=prepared_val,
                prepared_tests=prepared_tests,
                model=model,
                tokenizer=tokenizer,
            )
        else:
            scores = self._finetune(
                itr=itr,
                train=train,
                val=val,
                tests=tests,
                prepared_train=prepared_train,
                prepared_val=prepared_val,
                prepared_tests=prepared_tests,
                model=model,
                tokenizer=tokenizer,
                model_config=model_config,
            )

        all_scores = log_scores(
            dataset_name=self.dataset_config.pretty_name,
            metric_configs=self.dataset_config.task.metrics,
            scores=scores,
            model_id=model_config.model_id,
        )

        return all_scores, metadata_dict

    def _finetune(
        self,
        itr: tqdm,
        train: Dataset,
        val: Dataset,
        tests: list[Dataset],
        prepared_train: Dataset,
        prepared_val: Dataset,
        prepared_tests: list[Dataset],
        model: PreTrainedModel,
        tokenizer: Tokenizer,
        model_config: ModelConfig,
    ) -> dict[str, list[dict[str, float]]]:
        """Evaluate a model on a dataset through finetuning.

        Args:
            itr (tqdm.tqdm):
                The progress bar iterator.
            train (Dataset):
                The training dataset.
            val (Dataset):
                The validation dataset.
            tests (list[Dataset]):
                The bootstrapped test datasets.
            prepared_train (Dataset):
                The prepared training dataset.
            prepared_val (Dataset):
                The prepared validation dataset.
            prepared_tests (list[Dataset]):
                The prepared bootstrapped test datasets.
            model (PreTrainedModel):
                The model to evaluate.
            tokenizer (Tokenizer):
                The tokenizer to use.
            model_config (ModelConfig):
                The configuration of the model.

        Returns:
            dict[str, list[dict[str, float]]]:
                A dictionary containing the scores, with keys "test" and maybe "train",
                with values being lists of dicts containing the scores for each metric
                for each iteration.
        """
        return finetune(
            itr=itr,
            train=train,
            val=val,
            tests=tests,
            prepared_train=prepared_train,
            prepared_val=prepared_val,
            prepared_tests=prepared_tests,
            model=model,
            tokenizer=tokenizer,
            model_config=model_config,
            dataset_config=self.dataset_config,
            benchmark_config=self.benchmark_config,
            compute_metrics=self._compute_metrics,
            data_collator=self._load_data_collator(tokenizer=tokenizer, model=model),
        )

    def _generate(
        self,
        itr: tqdm,
        train: Dataset,
        val: Dataset,
        tests: list[Dataset],
        prepared_train: Dataset,
        prepared_val: Dataset,
        prepared_tests: list[Dataset],
        model: GenerativeModel,
        tokenizer: Tokenizer,
    ) -> dict[str, list[dict[str, float]]]:
        """Evaluate a model on a dataset through generation.

        Args:
            itr (tqdm.tqdm):
                The progress bar iterator.
            train (Dataset):
                The training dataset.
            val (Dataset):
                The validation dataset.
            tests (list[Dataset]):
                The bootstrapped test datasets.
            prepared_train (Dataset):
                The prepared training dataset.
            prepared_val (Dataset):
                The prepared validation dataset.
            prepared_tests (list[Dataset]):
                The prepared bootstrapped test datasets.
            num_iter (int):
                The number of iterations to run.
            rng (np.random.Generator):
                The random number generator.
            model (GenerativeModel):
                The model to evaluate.
            tokenizer (Tokenizer):
                The tokenizer to use for the model. If `None` then the model's
                tokenizer will be used.
            model_config (ModelConfig):
                The configuration of the model.

        Returns:
            dict[str, list[dict[str, float]]]:
                A dictionary containing the scores, with keys "test" and maybe "train",
                with values being lists of dicts containing the scores for each metric
                for each iteration.
        """
        return generate(
            itr=itr,
            train=train,
            val=val,
            tests=tests,
            prepared_train=prepared_train,
            prepared_val=prepared_val,
            prepared_tests=prepared_tests,
            model=model,
            tokenizer=tokenizer,
            data_collator=self._load_data_collator(tokenizer=tokenizer, model=model),
            compute_metrics=self._compute_metrics,
            benchmark_config=self.benchmark_config,
            dataset_config=self.dataset_config,
        )

    def _get_metadata(
        self,
        model: PreTrainedModel | GenerativeModel,
        tokenizer: Tokenizer,
    ) -> dict[str, int]:
        """Get metadata about the model.

        Args:
            model (PreTrainedModel or GenerativeModel):
                The model to get metadata about.
            tokenizer (Tokenizer):
                The tokenizer to get metadata about.

        Returns:
            dict[str, int]:
                A dictionary containing metadata about the model, with the keys being
                the metadata names and the values being the metadata values.
        """
        if hasattr(model.config, "num_params"):
            num_params = model.config.num_params
        elif isinstance(model, PreTrainedModel):
            num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        else:
            num_params = -1

        if hasattr(model.config, "model_max_length"):
            max_seq_length = getattr(model.config, "model_max_length")
        elif hasattr(tokenizer, "model_max_length"):
            max_seq_length = getattr(tokenizer, "model_max_length")
        else:
            max_seq_length = -1

        if hasattr(model.config, "vocab_size"):
            vocab_size = getattr(model.config, "vocab_size")
        elif hasattr(tokenizer, "vocab_size"):
            vocab_size = getattr(tokenizer, "vocab_size")
        else:
            vocab_size = -1

        # Store the metadata in a dictionary
        metadata_dict = dict(
            num_model_parameters=num_params,
            max_sequence_length=max_seq_length,
            vocabulary_size=vocab_size,
        )

        # Log the metadata
        logger.info(
            f"The model has {num_params:,} parameters, a vocabulary size of "
            f"{vocab_size:,} and a maximum sequence length of {max_seq_length:,}."
        )

        return metadata_dict

    def _load_data(
        self,
        num_iter: int,
        rng: np.random.Generator,
    ) -> tuple[Dataset, Dataset, list[Dataset]]:
        """Load the raw bootstrapped datasets.

        Args:
            num_iter (int):
                The number of iterations to run.
            rng (np.random.Generator):
                The random number generator to use.

        Returns:
            tuple[Dataset, Dataset, list[Dataset]]:
                A tuple containing the training, validation and test datasets.
        """
        # Download dataset from the HF Hub
        try:
            dataset_dict = load_dataset(
                path=self.dataset_config.huggingface_id,
                cache_dir=self.benchmark_config.cache_dir,
            )
        except HfHubHTTPError:
            raise InvalidBenchmark("The Hugging Face Hub seems to be down.")

        # If the dataset turns out not to be a DatasetDict, then we raise an error
        if not isinstance(dataset_dict, DatasetDict):
            raise InvalidBenchmark(
                f"Expected `dataset_dict` to be a `DatasetDict`, but got "
                f"{type(dataset_dict)}."
            )

        # Remove all other keys than 'train', 'val' and 'test'
        dataset_dict = DatasetDict(
            {key: dataset_dict[key] for key in ["train", "val", "test"]}
        )

        # Process the datasets
        dataset_dict = self._process_data(dataset_dict)

        # Extract the dataset splits
        train = dataset_dict["train"]
        val = dataset_dict["val"]
        test = dataset_dict["test"]

        # TEMP
        test = val

        # Remove empty examples from the datasets
        for text_feature in ["tokens", "doc", "text"]:
            if text_feature in train.features:
                train = train.filter(lambda x: len(x[text_feature]) > 0)
                val = val.filter(lambda x: len(x[text_feature]) > 0)
                test = test.filter(lambda x: len(x[text_feature]) > 0)

        # If we are testing then truncate the test set
        if self.benchmark_config.testing:
            test = test.select(range(128))

        # Bootstrap the test set
        test_bidxs = rng.integers(0, len(test), size=(num_iter, len(test)))
        tests = [test.select(test_bidxs[idx]) for idx in range(test_bidxs.shape[0])]

        return train, val, tests

    def _load_prepared_data(
        self,
        train: Dataset,
        val: Dataset,
        tests: list[Dataset],
        model_config: ModelConfig,
        hf_model_config: PretrainedConfig,
        tokenizer: Tokenizer,
    ) -> tuple[Dataset, Dataset, list[Dataset]]:
        """Load the data and prepare it for training.

        Args:
            train (Dataset):
                The raw training dataset.
            val (Dataset):
                The raw validation dataset.
            tests (list[Dataset]):
                The raw bootstrapped test datasets.
            model_config (ModelConfig):
                The model configuration.
            hf_model_config (PretrainedConfig):
                The Hugging Face model configuration.
            tokenizer (Tokenizer):
                The tokenizer.

        Returns:
            tuple[Dataset, Dataset, list[Dataset]]:
                A tuple containing the prepared training, validation and test datasets.
        """
        # Set up the preprocessing parameters
        preprocess_params: dict[str, Any] = dict(
            hf_model_config=hf_model_config,
            model_config=model_config,
            tokenizer=tokenizer,
        )

        # Prepare the train and validation datasets
        try:
            with tqdm(total=12, desc="Preprocessing data splits", leave=False) as pbar:
                prepared_train = self._preprocess_data(
                    train, split="train", **preprocess_params
                )
                pbar.update(1)

                prepared_val = self._preprocess_data(
                    val, split="val", **preprocess_params
                )
                pbar.update(1)

                prepared_tests: list[Dataset] = list()
                for itr_idx, test in enumerate(tests):
                    if model_config.task in ["text-generation", "conversational"]:
                        itr_seed = 4242 + itr_idx
                        shuffled_train = train.shuffle(seed=itr_seed)
                        num_few_shots = self.dataset_config.num_few_shot_examples

                        task = self.dataset_config.task.name
                        supertask = self.dataset_config.task.supertask
                        if supertask == "sequence-classification":
                            labels = it.cycle(self.dataset_config.task.labels)
                            few_shot_examples: list[dict] = list()
                            while len(few_shot_examples) < num_few_shots:
                                label = next(labels)
                                example = shuffled_train.filter(
                                    lambda x: x["label"].lower() == label.lower()
                                ).select(range(1))[0]
                                few_shot_examples.append(example)
                                shuffled_train = shuffled_train.filter(
                                    lambda x: x["text"] != example["text"]
                                )

                        elif task == "named-entity-recognition":
                            labels = it.cycle(
                                [
                                    label.lower()
                                    for label in self.dataset_config.task.labels
                                    if label.lower().startswith("b-")
                                ]
                            )
                            few_shot_examples = list()
                            while len(few_shot_examples) < num_few_shots:
                                label = next(labels)
                                example = shuffled_train.filter(
                                    lambda x: label
                                    in [tag.lower() for tag in x["label"]]
                                ).select(range(1))[0]
                                few_shot_examples.append(example)
                                shuffled_train = shuffled_train.filter(
                                    lambda x: x["doc"] != example["doc"]
                                )

                        else:
                            examples_df = shuffled_train.select(
                                range(num_few_shots)
                            ).to_pandas()
                            assert isinstance(examples_df, pd.DataFrame)
                            few_shot_examples = examples_df.to_dict("records")

                        random.seed(itr_seed)
                        random.shuffle(few_shot_examples)
                        preprocess_params["few_shot_examples"] = few_shot_examples
                    prepared_tests.append(
                        self._preprocess_data(test, split="test", **preprocess_params)
                    )
                    pbar.update(1)
        except ValueError:
            raise InvalidBenchmark(
                "Preprocessing of the training and validation datasets could not be "
                "done."
            )

        return prepared_train, prepared_val, prepared_tests

    def __call__(self, *args, **kwargs):
        return self.benchmark(*args, **kwargs)

    def _process_data(self, dataset_dict: DatasetDict) -> DatasetDict:
        """Process the data.

        Args:
            dataset_dict (DatasetDict):
                The dataset dictionary.

        Returns:
            DatasetDict:
                The processed dataset dictionary.
        """
        return dataset_dict

    @abstractmethod
    def _preprocess_data(self, dataset: Dataset, **kwargs) -> Dataset:
        """Preprocess a dataset.

        Args:
            dataset (Hugging Face dataset):
                The dataset to preprocess.
            kwargs:
                Extra keyword arguments containing objects used in preprocessing the
                dataset.

        Returns:
            Hugging Face dataset:
                The preprocessed dataset.
        """
        pass

    @abstractmethod
    def _load_data_collator(
        self,
        tokenizer: Tokenizer | None = None,
        model: PreTrainedModel | GenerativeModel | None = None,
    ):
        """Load the data collator used to prepare samples during finetuning.

        Args:
            tokenizer (Tokenizer or None, optional):
                A pretrained tokenizer. Can be None if the tokenizer is not used in the
                initialisation of the data collator. Defaults to None.
            model (PreTrainedModel or GenerativeModel or None, optional):
                A pretrained model. Can be None if the model is not used in the
                initialisation of the data collator. Defaults to None.

        Returns:
            Hugging Face data collator:
                The data collator.
        """
        pass

    def _compute_metrics(
        self,
        model_outputs_and_labels: tuple[list[int] | list[str], list[int] | list[str]],
        id2label: list[str],
    ) -> dict[str, float]:
        """Compute the metrics needed for evaluation.

        Args:
            model_outputs_and_labels (pair of sequences):
                The first sequence contains the model outputs and the second sequence
                contains the true labels.
            id2label (list or None, optional):
                Conversion of indices to labels. Defaults to None.

        Returns:
            dict:
                A dictionary with the names of the metrics as keys and the metric
                values as values.
        """
        model_outputs, labels = model_outputs_and_labels

        model_output_dtype = np.asarray(model_outputs).dtype
        if model_output_dtype in [np.float16, np.float32, np.float64]:
            predictions = np.asarray(model_outputs).argmax(axis=-1)
        else:
            predictions = model_outputs

        prompt_label_to_label_mapping = {
            prompt_label: label
            for label, prompt_label in self.dataset_config.prompt_label_mapping.items()
        }
        predictions = [
            id2label.index(prompt_label_to_label_mapping[pred.lower()])
            if isinstance(pred, str)
            else pred
            for pred in predictions
        ]

        labels = [
            id2label.index(prompt_label_to_label_mapping[label.lower()])
            if isinstance(label, str)
            else label
            for label in labels
        ]

        results: dict[str, float] = dict()
        for cfg in self.dataset_config.task.metrics:
            metric = self._metrics[cfg.name]
            score_dict: dict[str, float] | None = metric.compute(
                predictions=predictions,
                references=labels,
                **cfg.compute_kwargs,
            )
            if score_dict is not None:
                scores = score_dict[cfg.results_key]
                results[cfg.name] = scores
        return results
