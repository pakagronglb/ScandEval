<div align='center'>
<img src="https://raw.githubusercontent.com/saattrupdan/ScandEval/main/gfx/scandeval.png" width="517" height="217">
</div>

### Evaluation of pretrained language models on mono- or multilingual language tasks.

______________________________________________________________________
[![PyPI Status](https://badge.fury.io/py/scandeval.svg)](https://pypi.org/project/scandeval/)
[![Paper](https://img.shields.io/badge/arXiv-2304.00906-b31b1b.svg)](https://arxiv.org/abs/2304.00906)
[![License](https://img.shields.io/github/license/saattrupdan/ScandEval)](https://github.com/saattrupdan/ScandEval/blob/main/LICENSE)
[![LastCommit](https://img.shields.io/github/last-commit/saattrupdan/ScandEval)](https://github.com/saattrupdan/ScandEval/commits/main)
[![Code Coverage](https://img.shields.io/badge/Coverage-64%25-yellow.svg)](https://github.com/saattrupdan/ScandEval/tree/main/tests)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.0-4baaaa.svg)](https://github.com/saattrupdan/ScandEval/blob/main/CODE_OF_CONDUCT.md)


## Installation
To install the package simply write the following command in your favorite terminal:
```
$ pip install scandeval
```

## Quickstart
### Benchmarking from the Command Line
The easiest way to benchmark pretrained models is via the command line interface. After
having installed the package, you can benchmark your favorite model like so:
```
$ scandeval --model-id <model-id>
```

Here `model_id` is the HuggingFace model ID, which can be found on the [HuggingFace
Hub](https://huggingface.co/models). By default this will benchmark the model on all
the datasets eligible. If you want to benchmark on a specific dataset, this can be done
via the `--dataset` flag. This will for instance evaluate the model on the
`AngryTweets` dataset:
```
$ scandeval --model-id <model-id> --dataset angry-tweets
```

We can also separate by language. To benchmark all Danish models on all Danish
datasets, say, this can be done using the `language` tag, like so:
```
$ scandeval --language da
```

Multiple models, datasets and/or languages can be specified by just attaching multiple
arguments. Here is an example with two models:
```
$ scandeval --model-id <model-id1> --model-id <model-id2> --dataset angry-tweets
```

The specific model version to use can also be added after the suffix '@':
```
$ scandeval --model-id <model-id>@<commit>
```

It can be a branch name, a tag name, or a commit id. It defaults to 'main' for latest.

See all the arguments and options available for the `scandeval` command by typing
```
$ scandeval --help
```

### Benchmarking from a Script
In a script, the syntax is similar to the command line interface. You simply initialise
an object of the `Benchmarker` class, and call this benchmark object with your favorite
models and/or datasets:
```
>>> from scandeval import Benchmarker
>>> benchmark = Benchmarker()
>>> benchmark('<model-id>')
```

To benchmark on a specific dataset, you simply specify the second argument, shown here
with the `AngryTweets` dataset again:
```
>>> benchmark('<model_id>', 'angry-tweets')
```

If you want to benchmark a subset of all the models on the Hugging Face Hub, you can
specify several parameters in the `Benchmarker` initializer to narrow down the list of
models to the ones you care about. As a simple example, the following would benchmark
all the Nynorsk models on Nynorsk datasets:
```
>>> benchmark = Benchmarker(language='nn')
>>> benchmark()
```


## Citing ScandEval
If you want to cite the framework then feel free to use this:

```
@inproceedings{nielsen2023scandeval,
  title={ScandEval: A Benchmark for Scandinavian Natural Language Processing},
  author={Nielsen, Dan Saattrup},
  booktitle={The 24rd Nordic Conference on Computational Linguistics},
  year={2023}
}
```


## Remarks
The image used in the logo has been created by the amazing [Scandinavia and the
World](https://satwcomic.com/) team. Go check them out!


## Project structure
```
.
├── .github
│   └── workflows
│       └── ci.yaml
├── .gitignore
├── .pre-commit-config.yaml
├── CHANGELOG.md
├── LICENSE
├── README.md
├── docs
├── gfx
│   └── scandeval.png
├── makefile
├── poetry.lock
├── poetry.toml
├── pyproject.toml
├── src
│   ├── scandeval
│   │   ├── __init__.py
│   │   ├── __pycache__
│   │   ├── benchmark_config_factory.py
│   │   ├── benchmark_dataset.py
│   │   ├── benchmarker.py
│   │   ├── callbacks.py
│   │   ├── cli.py
│   │   ├── config.py
│   │   ├── dataset_configs.py
│   │   ├── dataset_factory.py
│   │   ├── dataset_tasks.py
│   │   ├── enums.py
│   │   ├── exceptions.py
│   │   ├── finetuning.py
│   │   ├── generation.py
│   │   ├── languages.py
│   │   ├── model_config.py
│   │   ├── model_loading.py
│   │   ├── model_setups
│   │   │   ├── __init__.py
│   │   │   ├── __pycache__
│   │   │   ├── fresh.py
│   │   │   ├── hf.py
│   │   │   ├── local.py
│   │   │   ├── openai.py
│   │   │   └── utils.py
│   │   ├── named_entity_recognition.py
│   │   ├── openai_models.py
│   │   ├── protocols.py
│   │   ├── question_answering.py
│   │   ├── question_answering_trainer.py
│   │   ├── scores.py
│   │   ├── sequence_classification.py
│   │   ├── speed_benchmark.py
│   │   ├── text_to_text.py
│   │   ├── types.py
│   │   └── utils.py
│   └── scripts
│       ├── __pycache__
│       ├── create_angry_tweets.py
│       ├── create_dane.py
│       ├── create_mim_gold_ner.py
│       ├── create_mlsum.py
│       ├── create_no_sammendrag.py
│       ├── create_nordjylland_news.py
│       ├── create_norec.py
│       ├── create_norne.py
│       ├── create_rrn.py
│       ├── create_scala.py
│       ├── create_scandiqa.py
│       ├── create_suc3.py
│       ├── create_swedn.py
│       ├── create_swerec.py
│       ├── create_wiki_lingua_nl.py
│       ├── create_wikiann_fo.py
│       ├── fill_in_missing_model_metadata.py
│       ├── fix_dot_env_file.py
│       ├── load_ud_pos.py
│       └── versioning.py
└── tests
    ├── __init__.py
    ├── __pycache__
    ├── conftest.py
    ├── test_benchmark_config_factory.py
    ├── test_benchmark_dataset.py
    ├── test_benchmarker.py
    ├── test_callbacks.py
    ├── test_cli.py
    ├── test_config.py
    ├── test_dataset_configs.py
    ├── test_dataset_factory.py
    ├── test_dataset_tasks.py
    ├── test_enums.py
    ├── test_exceptions.py
    ├── test_languages.py
    ├── test_model_config.py
    ├── test_model_loading.py
    ├── test_named_entity_recognition.py
    ├── test_openai_models.py
    ├── test_question_answering.py
    ├── test_question_answering_trainer.py
    ├── test_scores.py
    ├── test_sequence_classification.py
    ├── test_speed_benchmark.py
    ├── test_types.py
    └── test_utils.py
```
