# Training dataset

`combined.csv` contains 254,010 unique texts with these columns:

```text
text,hate,harrasment,violence,spam
```

| Target | Mapping |
| --- | --- |
| hate | Jigsaw `identity_hate` |
| harrasment | Any Jigsaw `toxic`, `severe_toxic`, `obscene`, or `insult` |
| violence | Jigsaw `threat` |
| spam | Enron `label` |

Labels are binary: `1` for positive and `0` otherwise. Categories not annotated
by a source are also set to `0`: Jigsaw rows have `spam=0`, and Enron rows have
`hate=harrasment=violence=0`. These are assumed negatives, not additional source
annotations. Harassment and violence are approximate mappings from the available
source labels. No loss masking is needed.

The pandas script combines both sources' train and labeled test examples, removes empty
texts, merges duplicate texts, shuffles them, and saves one CSV. Create your own
train/validation/test split before training.

Run from the project root:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python dataset/create.py
```

Downloads are cached in `dataset/raw/` and reused automatically.

Sources: [Jigsaw](https://huggingface.co/datasets/thesofakillers/jigsaw-toxic-comment-classification-challenge)
and [Enron](https://huggingface.co/datasets/SetFit/enron_spam).
Jigsaw's card lists CC-BY-SA-3.0; Enron's card links to its upstream dataset without
declaring a license. Source revisions are pinned in the script.
