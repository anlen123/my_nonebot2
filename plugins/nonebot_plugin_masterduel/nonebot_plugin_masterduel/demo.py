import httpx
from bs4 import BeautifulSoup

import meilisearch, json, re, time, requests, zipfile, os, httpx
from bs4 import BeautifulSoup


def read_cards_ids() -> tuple:
    cards_list = []
    with open('cards.json') as f:
        cards_dict = json.loads(f.read())
    for card in list(cards_dict.values()):
        id = card.get('cid')
        cards_list.append(str(id))

    return cards_list, cards_dict


def is_include_id_by_index(id: str, index: meilisearch.models.document.Document):
    try:
        index.get_document(id)
        return True
    except meilisearch.errors.MeilisearchApiError as ee:
        return False


class Ygo_Card:

    def __init__(self):
        self.id = None
        self.name = None
        self.desc = None
        self.attribute = None
        self.img = None

    def json_format(self):
        return {
            "id": self.id,
            "name": self.name,
            "attribute": self.attribute,
            "desc": self.desc,
            "img": self.img,
        }

    def gou_zao(self, x: meilisearch.models.document.Document):
        self.id = x.id
        self.name = x.name
        self.desc = x.desc
        self.attribute = x.attribute
        self.img = x.img


if __name__ == '__main__':
    client = meilisearch.Client('http://localhost:7700')
    old_index_name = 'd_cards'
    index = client.get_index(old_index_name)
    ids, _ = read_cards_ids()
    for id in ids:
        if is_include_id_by_index(id, index):
            x = index.get_document(id)
            x.img = str(x.img)[:-5]
            card = Ygo_Card()
            card.gou_zao(x)
            index.update_documents_json(card.json_format())
