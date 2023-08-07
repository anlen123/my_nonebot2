#!/Users/liuhq/miniconda3/bin/python
# -*- coding: utf-8 -*-


import meilisearch
import json
import re
import time


class Ygo_Card:

    def __init__(self, card: meilisearch.models.document.Document):
        self.id = card.id
        self.name = card.name
        self.alias = card.alias
        self.desc = card.desc
        self.attribute_desc = card.attribute_desc
        self.type = card.type
        self.monsters_type = card.monsters_type
        self.ATK = card.ATK
        self.DEF = card.DEF
        self.lv = card.lv
        self.race = card.race
        self.attribute = card.attribute
        self.img = card.img

    def json_format(self):
        return {
            "id": self.id,
            "name": self.name,
            "alias": self.alias,
            "attribute_desc": self.attribute_desc,
            "type": self.type,
            "ATK": self.ATK,
            "DEF": self.DEF,
            "lv": self.lv,
            "race": self.race,
            "attribute": self.attribute,
            "desc": self.desc,
            "img": self.img,
            "monsters_type": self.monsters_type
        }


def read_cards_ids() -> list:
    with open('ygo_cards.json') as f:
        cards = list(json.loads(f.read()).values())
    cards_list = []
    for card in cards:
        idd = card.get('cid')
        cards_list.append(idd)

    return cards_list


def write_new_cards(old_index: meilisearch.index.Index, new_index: meilisearch.index.Index, is_delete: bool):
    client = meilisearch.Client('http://localhost:7700')
    alias_index = client.get_index('d_cards_alias')
    if is_delete:
        new_index.delete_all_documents()
        time.sleep(10)
    ygos = []
    for id in ids:
        try:
            new_index.get_document(id)
        except meilisearch.errors.MeilisearchApiError as e:
            card = old_index.get_document(id)
            new_ygo = Ygo_Card(card)
            alias_name = new_ygo.name
            try:
                alias_name = alias_index.get_document(new_ygo.id).name
                new_ygo.alias = alias_name
            except meilisearch.errors.MeilisearchApiError as ee:
                pass
            new_card_json = new_ygo.json_format()
            ygos.append(new_card_json)
        if len(ygos) >= 200:
            new_index.add_documents_json(ygos)
            ygos = []
    while ygos:
        new_index.add_documents_json(ygos)
        ygos = []


if __name__ == '__main__':
    ids = read_cards_ids()
    client = meilisearch.Client('http://localhost:7700')
    old_index = client.get_index('d_cards_version4')
    client.create_index('d_cards_version5')
    time.sleep(3)
    new_index = client.get_index('d_cards_version5')
    write_new_cards(old_index, new_index, True)
    client.index('d_cards_version4').update_settings({
        "distinctAttribute": "id",
        "filterableAttributes": [
            "alias",
            "attribute",
            "race",
            "lv",
            "type",
            "ATK",
            "DEF",
            "name",
            "monsters_type"
        ],
        "searchableAttributes": [
            "alias",
            "attribute",
            "race",
            "lv",
            "type",
            "ATK",
            "DEF",
            "name",
            "desc",
            "attribute_desc"
            "monsters_type"
        ],
        "sortableAttributes": [
            "lv",
            "ATK",
            "DEF"
        ],
        "pagination": {
            "maxTotalHits": 5000
        },
        "faceting": {
            "maxValuesPerFacet": 200
        }
    })
