from elasticsearch import helpers

from .base import Storage
from ..query.queryset import BaseQuerySet
from ..query.query import QueryGroup
from ..query.query import RawQuery
from modularodm.exceptions import NoResultsFound, MultipleResultsFound


class ElasticsearchQuerySet(BaseQuerySet):

    def __init__(self, schema, cursor):

        super(ElasticsearchQuerySet, self).__init__(schema)
        self.data = cursor

    def __getitem__(self, index, raw=False):
        super(ElasticsearchQuerySet, self).__getitem__(index)
        key = self.data[index][self.primary]
        if raw:
            return key
        return self.schema.load(key)

    def __iter__(self, raw=False):
        keys = [obj[self.primary] for obj in self.data.clone()]
        if raw:
            return keys
        return (self.schema.load(key) for key in keys)

    def __len__(self):
        return len(self.data)

    count = __len__

    def get_key(self, index):
        return self.__getitem__(index, raw=True)

    def get_keys(self):
        return list(self.__iter__(raw=True))

    def sort(self, *keys):

        sort_key = []

        for key in keys:

            if key.startswith('-'):
                key = key.lstrip('-')
                sign = pyelasticsearch.DESCENDING
            else:
                sign = pyelasticsearch.ASCENDING

            sort_key.append((key, sign))

        self.data = self.data.sort(sort_key)
        return self

    def offset(self, n):

        self.data = self.data.skip(n)
        return self

    def limit(self, n):

        self.data = self.data.limit(n)
        return self

class ElasticsearchStorage(Storage):

    QuerySet = ElasticsearchQuerySet

    def __init__(self, client, es_index, collection, ):
        self.client = client
        self.collection = collection
        self.es_index = es_index


    def find(self, query=None, **kwargs):
        elasticsearch_query = self._translate_query(query)

        matches = []
        for results in helpers.scan(
            self.client,
            query=elasticsearch_query,
            index=self.es_index,
            doc_type=self.collection,
        ):
            matches.append(results)


        return matches

    def find_one(self, query=None, **kwargs):
        """ Gets a single object from the collection.

        If no matching documents are found, raises ``NoResultsFound``.
        If >1 matching documents are found, raises ``MultipleResultsFound``.

        :params: One or more ``Query`` or ``QuerySet`` objects may be passed

        :returns: The selected document
        """
        elasticsearch_query = self._translate_query(query)
        matches = self.client.search(
            index=self.es_index,
            doc_type=self.collection,
            body=elasticsearch_query,
        )['hits']['hits']

        if len(matches) == 1:
            return matches[0]

        if len(matches) == 0:
            raise NoResultsFound()

        raise MultipleResultsFound(
            'Query for find_one must return exactly one result; '
            'returned {0}'.format(len(matches))
        )

    def get(self, primary_name, key):
        return self.client.get(index=self.es_index, doc_type=self.collection, id=key)

    def insert(self, primary_name, key, value):
        self.client.create(index=self.es_index, doc_type=self.collection, id=key, body=value)

    def update(self, query, data):
        for doc in self.find(query):
            self.client.update(
                index=self.es_index,
                doc_type=self.collection, id=doc['_id'],
                body={'doc': data}
            )

    def remove(self, query=None):
        elasticsearch_query = self._translate_query(query)
        self.client.delete_by_query(
            index=self.es_index,
            doc_type=self.collection,
            body=elasticsearch_query
        )

    def flush(self):
        pass

    def __repr__(self):
        return self.find()

    def _translate_query(self, query=None, elasticsearch_query=None):
        """

        """
        elasticsearch_query = elasticsearch_query or {}

        if isinstance(query, RawQuery):
            attribute, operator, argument = \
                query.attribute, query.operator, query.argument

            if operator == 'eq':
                elasticsearch_query[attribute] = argument

            elif operator in COMPARISON_OPERATORS:
                elasticsearch_operator = '$' + operator
                if attribute not in elasticsearch_query:
                    elasticsearch_query[attribute] = {}
                elasticsearch_query[attribute][elasticsearch_operator] = argument

            elif operator in STRING_OPERATORS:
                elasticsearch_operator = '$regex'
                elasticsearch_regex = prepare_query_value(operator, argument)
                if attribute not in elasticsearch_query:
                    elasticsearch_query[attribute] = {}
                elasticsearch_query[attribute][elasticsearch_operator] = elasticsearch_regex

        elif isinstance(query, QueryGroup):

            if query.operator == 'and':
                elasticsearch_query = {}
                for node in query.nodes:
                    part = self._translate_query(node, elasticsearch_query)
                    elasticsearch_query.update(part)
                return elasticsearch_query

            elif query.operator == 'or':
                return {'$or' : [self._translate_query(node) for node in query.nodes]}

            elif query.operator == 'not':
                return {'$not' : self._translate_query(query.nodes[0])}

            else:
                raise ValueError('QueryGroup operator must be <and>, <or>, or <not>.')

        elif query is None:
            return {}

        else:
            raise TypeError('Query must be a QueryGroup or Query object.')

        return elasticsearch_query