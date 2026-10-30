import csv
import json

import pandas as pd

from dataset.create import COLUMNS, JIGSAW_LABELS, build, map_enron, map_jigsaw


def test_label_mapping():
    labels = dict.fromkeys(JIGSAW_LABELS, 0)
    labels.update(identity_hate=1, threat=1, insult=1)
    assert map_jigsaw(pd.DataFrame([dict(comment_text='Text', **labels)])).iloc[0].tolist() == ['Text', 1, 1, 1, 0]
    assert map_enron(pd.DataFrame([{'text': 'Spam', 'label': 1}])).iloc[0].tolist() == ['Spam', 0, 0, 0, 1]
    assert map_enron(pd.DataFrame([{'text': 'Ham', 'label': 0}])).iloc[0].tolist() == ['Ham', 0, 0, 0, 0]


def test_build_skips_unlabeled_and_empty_text_and_merges_duplicates(tmp_path, monkeypatch):
    def write_csv(name, columns, rows):
        with (tmp_path / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    labels = dict.fromkeys(JIGSAW_LABELS, 0)
    write_csv('train.csv', ['id', 'comment_text', *JIGSAW_LABELS], [
        dict(id='1', comment_text='Shared text', **{**labels, 'insult': '1'}),
        dict(id='2', comment_text='  ', **labels)])
    write_csv('test.csv', ['id', 'comment_text'], [
        dict(id='3', comment_text='Unlabeled'), dict(id='4', comment_text='Threat')])
    write_csv('test_labels.csv', ['id', *JIGSAW_LABELS], [
        dict(id='3', **dict.fromkeys(JIGSAW_LABELS, '-1')),
        dict(id='4', **{**labels, 'threat': '1'})])
    (tmp_path / 'train.jsonl').write_text(json.dumps(dict(text='Shared text', label=1)) + '\n')
    (tmp_path / 'test.jsonl').write_text(json.dumps(dict(text='Ham', label=0)) + '\n')
    monkeypatch.setattr('dataset.create.download', lambda source, filename: tmp_path / filename)
    output = tmp_path / 'combined.csv'
    assert build(output) == 3
    with output.open(newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == COLUMNS
        rows = {row['text']: row for row in reader}
    assert rows['Shared text']['harrasment'] == rows['Shared text']['spam'] == '1'
    assert rows['Threat']['violence'] == '1'
    assert rows['Ham']['hate'] == '0'
    assert 'Unlabeled' not in rows
    assert all(row[label] in {'0', '1'} for row in rows.values() for label in COLUMNS[1:])
