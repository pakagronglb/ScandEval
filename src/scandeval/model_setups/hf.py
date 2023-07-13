"""Model setup for Hugging Face Hub models."""

import logging
import warnings
from json import JSONDecodeError
from typing import Type, TypedDict

import torch
from huggingface_hub import HfApi, ModelFilter
from huggingface_hub.hf_api import RepositoryNotFoundError
from requests import RequestException
from transformers import AutoConfig, AutoTokenizer, PretrainedConfig, PreTrainedModel

from ..config import BenchmarkConfig, DatasetConfig, ModelConfig
from ..enums import Framework, ModelType
from ..exceptions import HuggingFaceHubDown, InvalidBenchmark, NoInternetConnection
from ..languages import get_all_languages
from ..utils import (
    HiddenPrints,
    block_terminal_output,
    create_model_cache_dir,
    get_class_by_name,
    internet_connection_available,
    model_is_generative,
)
from .base import GenerativeModel, Tokenizer
from .utils import align_model_and_tokenizer, setup_model_for_question_answering

logger = logging.getLogger(__package__)


class LoadingArguments(TypedDict):
    revision: str
    token: str | bool
    cache_dir: str
    trust_remote_code: bool


class HFModelSetup:
    """Model setup for Hugging Face Hub models.

    Args:
        benchmark_config (BenchmarkConfig):
            The benchmark configuration.

    Attributes:
        benchmark_config (BenchmarkConfig):
            The benchmark configuration.
    """

    def __init__(self, benchmark_config: BenchmarkConfig) -> None:
        self.benchmark_config = benchmark_config

    def model_exists(self, model_id: str) -> bool:
        """Check if a model ID denotes an OpenAI model.

        Args:
            model_id (str):
                The model ID.

        Returns:
            bool:
                Whether the model exists on OpenAI.
        """
        # Extract the revision from the model_id, if present
        model_id, revision = (
            model_id.split("@") if "@" in model_id else (model_id, "main")
        )

        # Connect to the Hugging Face Hub API
        hf_api = HfApi()

        # Get the model info, and return it
        try:
            if isinstance(self.benchmark_config.token, bool):
                token = None
            else:
                token = self.benchmark_config.token
            hf_api.model_info(repo_id=model_id, revision=revision, token=token)
            return True

        # If the repository was not found on Hugging Face Hub then raise that error
        except RepositoryNotFoundError:
            return False

        # If fetching from the Hugging Face Hub failed in a different way then throw a
        # reasonable exception
        except RequestException:
            if internet_connection_available():
                raise HuggingFaceHubDown()
            else:
                raise NoInternetConnection()

    def get_model_config(self, model_id: str) -> ModelConfig:
        """Fetches configuration for an OpenAI model.

        Args:
            model_id (str):
                The model ID of the model.

        Returns:
            ModelConfig:
                The model configuration.
        """
        # Extract the revision from the model ID, if it is specified
        if "@" in model_id:
            model_id_without_revision, revision = model_id.split("@", 1)
        else:
            model_id_without_revision = model_id
            revision = "main"

        # Extract the author and model name from the model ID
        author: str | None
        if "/" in model_id_without_revision:
            author, model_name = model_id_without_revision.split("/")
        else:
            author = None
            model_name = model_id_without_revision

        # Attempt to fetch model data from the Hugging Face Hub
        try:
            # Define the API object
            api: HfApi = HfApi()

            # Fetch the model metadata
            models = api.list_models(
                filter=ModelFilter(author=author, model_name=model_name),
                token=self.benchmark_config.token,
            )

            # Filter the models to only keep the one with the specified model ID
            models = [
                model for model in models if model.modelId == model_id_without_revision
            ]

            # Check that the model exists. If it does not then raise an error
            if len(models) == 0:
                raise InvalidBenchmark(
                    f"The model {model_id} does not exist on the Hugging Face Hub."
                )

            # Fetch the model tags
            tags: list[str] = models[0].tags

            # Extract the framework, which defaults to PyTorch
            framework = Framework.PYTORCH
            if "pytorch" in tags:
                pass
            elif "jax" in tags:
                framework = Framework.JAX
            elif "spacy" in tags:
                raise InvalidBenchmark("SpaCy models are not supported.")
            elif "tf" in tags or "tensorflow" in tags or "keras" in tags:
                raise InvalidBenchmark("TensorFlow/Keras models are not supported.")

            # Extract the model task, which defaults to 'fill-mask'
            model_task: str | None = models[0].pipeline_tag
            if model_task is None:
                model_task = "fill-mask"

            # Get list of all language codes
            language_mapping = get_all_languages()
            language_codes = list(language_mapping.keys())

            # Construct the model config
            model_config = ModelConfig(
                model_id=models[0].modelId,
                framework=framework,
                task=model_task,
                languages=[
                    language_mapping[tag] for tag in tags if tag in language_codes
                ],
                revision=revision,
                model_type=ModelType.HF,
                model_cache_dir=create_model_cache_dir(
                    cache_dir=self.benchmark_config.cache_dir,
                    model_id=model_id,
                ),
            )

        # If fetching from the Hugging Face Hub failed then throw a reasonable
        # exception
        except RequestException:
            if internet_connection_available():
                raise HuggingFaceHubDown()
            else:
                raise NoInternetConnection()

        return model_config

    def load_model(
        self, model_config: ModelConfig, dataset_config: DatasetConfig
    ) -> tuple[Tokenizer, PreTrainedModel | GenerativeModel]:
        """Load an OpenAI model.

        Args:
            model_config (ModelConfig):
                The model configuration.
            dataset_config (DatasetConfig):
                The dataset configuration.

        Returns:
            pair of (tokenizer, model):
                The tokenizer and model.
        """
        config: PretrainedConfig
        block_terminal_output()

        model_id = model_config.model_id
        supertask = dataset_config.task.supertask
        from_flax = model_config.framework == Framework.JAX
        ignore_mismatched_sizes = False

        if self.benchmark_config.load_in_4bit is not None:
            load_in_4bit = self.benchmark_config.load_in_4bit
        else:
            load_in_4bit = (
                model_config.task == "text-generation"
                and self.benchmark_config.device == torch.device("cuda")
            )

        loading_kwargs: LoadingArguments = {
            "revision": model_config.revision,
            "token": self.benchmark_config.token,
            "cache_dir": model_config.model_cache_dir,
            "trust_remote_code": self.benchmark_config.trust_remote_code,
        }

        while True:
            try:
                try:
                    config = AutoConfig.from_pretrained(
                        model_id,
                        num_labels=dataset_config.num_labels,
                        id2label=dataset_config.id2label,
                        label2id=dataset_config.label2id,
                        **loading_kwargs,
                    )
                except KeyError as e:
                    key = e.args[0]
                    raise InvalidBenchmark(
                        f"The model config for the model {model_id!r} could not "
                        f"be loaded, as the key {key!r} was not found in the "
                        "config."
                    )

                # Get the model class associated with the supertask
                if model_config.task == "text-generation":
                    model_cls_supertask = "causal-l-m"
                else:
                    model_cls_supertask = supertask
                model_cls_or_none: Type[PreTrainedModel] | None = get_class_by_name(
                    class_name=f"auto-model-for-{model_cls_supertask}",
                    module_name="transformers",
                )

                # If the model class could not be found then raise an error
                if not model_cls_or_none:
                    raise InvalidBenchmark(
                        f"The supertask {supertask!r} does not correspond to a "
                        "Hugging Face AutoModel type (such as "
                        "`AutoModelForSequenceClassification`)."
                    )

                # If the model is a DeBERTaV2 model then we ensure that
                # `pooler_hidden_size` is the same size as `hidden_size`
                if config.model_type == "deberta-v2":
                    config.pooler_hidden_size = config.hidden_size

                # Load the model
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning)
                    with HiddenPrints():
                        try:
                            model_or_tuple = model_cls_or_none.from_pretrained(
                                model_config.model_id,
                                config=config,
                                from_flax=from_flax,
                                ignore_mismatched_sizes=ignore_mismatched_sizes,
                                load_in_4bit=load_in_4bit,
                                # bnb_4bit_use_double_quant=load_in_4bit,  # TEMP?
                                **loading_kwargs,
                            )
                        except KeyError as e:
                            if not ignore_mismatched_sizes:
                                ignore_mismatched_sizes = True
                                continue
                            else:
                                raise InvalidBenchmark(str(e))
                if isinstance(model_or_tuple, tuple):
                    model = model_or_tuple[0]
                else:
                    model = model_or_tuple
                break

            except (OSError, ValueError) as e:
                # If `from_flax` is False but only Flax models are available then try
                # again with `from_flax` set to True
                if not from_flax and "Use `from_flax=True` to load this model" in str(
                    e
                ):
                    from_flax = True
                    continue

                self._handle_loading_exception(exception=e, model_id=model_id)

        # Set up the model for question answering
        if supertask == "question-answering":
            model = setup_model_for_question_answering(model=model)

        tokenizer = self._load_tokenizer(model=model, model_id=model_id)

        # Align the model and the tokenizer
        model, tokenizer = align_model_and_tokenizer(
            model=model,
            tokenizer=tokenizer,
            generation_length=dataset_config.max_generated_tokens,
            raise_errors=self.benchmark_config.raise_errors,
        )

        model.eval()
        if not load_in_4bit:
            model.to(self.benchmark_config.device)

        return tokenizer, model

    def _load_tokenizer(
        self,
        model: PreTrainedModel | GenerativeModel,
        model_id: str,
    ) -> Tokenizer:
        """Load the tokenizer.

        Args:
            model (PreTrainedModel or GenerativeModel):
                The model, which is used to determine whether to add a prefix space to
                the tokens.
            model_id (str):
                The model identifier. Used for logging.

        Returns:
            Tokenizer:
                The loaded tokenizer.
        """
        # If the model is a subclass of a RoBERTa model then we have to add a prefix
        # space to the tokens, by the way the model is constructed.
        prefix_models = ["Roberta", "GPT", "Deberta"]
        prefix = any(model_type in type(model).__name__ for model_type in prefix_models)
        padding_side = "left" if model_is_generative(model=model) else "right"
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            try:
                return AutoTokenizer.from_pretrained(
                    model_id,
                    add_prefix_space=prefix,
                    use_fast=True,
                    verbose=False,
                    padding_side=padding_side,
                    truncation_side=padding_side,
                )
            except (JSONDecodeError, OSError):
                raise InvalidBenchmark(
                    f"Could not load tokenizer for model {model_id!r}."
                )

    @staticmethod
    def _handle_loading_exception(exception: Exception, model_id: str) -> None:
        if "checkpoint seems to be incorrect" in str(exception):
            raise InvalidBenchmark(
                f"The model {model_id!r} has an incorrect checkpoint."
            )
        if "trust_remote_code" in str(exception):
            raise InvalidBenchmark(
                f"Loading the model {model_id!r} needs to trust remote code. "
                "If you trust the suppliers of this model, then you can enable "
                "this by setting the `--trust-remote-code` flag."
            )
        raise InvalidBenchmark(
            f"The model {model_id} either does not exist on the Hugging Face "
            "Hub, or it has no frameworks registered, or it is a private "
            "model. If it *does* exist on the Hub and is a public model then "
            "please ensure that it has a framework registered. If it is a "
            "private model then enable the `--token` flag and make "
            "sure that you are logged in to the Hub via the "
            "`huggingface-cli login` command."
        )
