"""Download Jigsaw and Enron, map their labels, and write combined.csv."""
from pathlib import Path

import httpx
import pandas as pd

FOLDER = Path(__file__).resolve().parent
COLUMNS = ['text', 'hate', 'harrasment', 'violence', 'spam']
JIGSAW_LABELS = ['toxic', 'severe_toxic', 'obscene', 'threat', 'insult', 'identity_hate']
SOURCES = {
    'jigsaw': ('thesofakillers/jigsaw-toxic-comment-classification-challenge',
               '50f3c926ec8c07b440cb17b55d8cd80e8484497e'),
    'enron': ('SetFit/enron_spam', '1916f66c89d52221ae33eb57d44498b4f3a5df22'),
}


def download(source, filename):
    repo, revision = SOURCES[source]
    path = FOLDER / 'raw' / source / revision / filename
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f'Downloading {source}/{filename}', flush=True)
        url = f'https://huggingface.co/datasets/{repo}/resolve/{revision}/{filename}'
        temporary = path.with_suffix(path.suffix + '.part')
        with httpx.stream('GET', url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with temporary.open('wb') as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        temporary.replace(path)
    return path


def map_jigsaw(frame):
    return pd.DataFrame({
        'text': frame['comment_text'],
        'hate': frame['identity_hate'],
        'harrasment': frame[['toxic', 'severe_toxic', 'obscene', 'insult']].max(axis=1),
        'violence': frame['threat'],
        'spam': 0,
    })


def map_enron(frame):
    return pd.DataFrame({'text': frame['text'], 'hate': 0, 'harrasment': 0,
                         'violence': 0, 'spam': frame['label']})


def build(output=FOLDER / 'combined.csv'):
    train = pd.read_csv(download('jigsaw', 'train.csv'), dtype={'id': str}, keep_default_na=False)
    test = pd.read_csv(download('jigsaw', 'test.csv'), dtype={'id': str}, keep_default_na=False)
    labels = pd.read_csv(download('jigsaw', 'test_labels.csv'), dtype={'id': str})
    test = test.merge(labels, on='id')
    test = test[test[JIGSAW_LABELS].isin([0, 1]).all(axis=1)]

    frames = [map_jigsaw(train), map_jigsaw(test)]
    for split in ['train', 'test']:
        frames.append(map_enron(pd.read_json(download('enron', f'{split}.jsonl'), lines=True)))
    data = pd.concat(frames, ignore_index=True)
    data['text'] = (data['text'].fillna('').str.replace('\r\n', '\n', regex=False)
                    .str.replace('\r', '\n', regex=False).str.strip())
    data = data[data['text'] != '']
    # Merge duplicate texts and keep any known positive label.
    data = data.groupby('text', sort=False, as_index=False)[COLUMNS[1:]].max()
    data[COLUMNS[1:]] = data[COLUMNS[1:]].astype(int)
    data = data.sample(frac=1, random_state=42)
    data[COLUMNS].to_csv(output, index=False)
    return len(data)


if __name__ == '__main__':
    print(f'Saved {build():,} rows to {FOLDER / "combined.csv"}')
