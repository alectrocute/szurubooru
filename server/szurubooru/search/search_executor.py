import re
import sqlalchemy
from szurubooru import errors
from szurubooru.search import criteria

class SearchExecutor(object):
    '''
    Class for search parsing and execution. Handles plaintext parsing and
    delegates sqlalchemy filter decoration to SearchConfig instances.
    '''

    def __init__(self, search_config):
        self._search_config = search_config

    def execute(self, query_text, page, page_size):
        '''
        Parse input and return tuple containing total record count and filtered
        entities.
        '''
        filter_query = self._prepare(query_text)
        entities = filter_query \
            .offset((page - 1) * page_size).limit(page_size).all()
        count_query = filter_query.statement \
            .with_only_columns([sqlalchemy.func.count()]) \
            .order_by(None)
        count = filter_query.session \
            .execute(count_query) \
            .scalar()
        return (count, entities)

    def execute_and_serialize(self, ctx, serializer):
        query = ctx.get_param_as_string('query')
        page = ctx.get_param_as_int('page', default=1, min=1)
        page_size = ctx.get_param_as_int('pageSize', default=100, min=1, max=100)
        count, entities = self.execute(query, page, page_size)
        return {
            'query': query,
            'page': page,
            'pageSize': page_size,
            'total': count,
            'results': [serializer(entity) for entity in entities],
        }

    def _prepare(self, query_text):
        ''' Parse input and return SQLAlchemy query. '''
        query = self._search_config.create_query() \
            .options(sqlalchemy.orm.lazyload('*'))
        for token in re.split(r'\s+', (query_text or '').lower()):
            if not token:
                continue
            negated = False
            while token[0] == '-':
                token = token[1:]
                negated = not negated

            if ':' in token:
                key, value = token.split(':', 2)
                query = self._handle_key_value(query, key, value, negated)
            else:
                query = self._handle_anonymous(
                    query, self._create_criterion(token, negated))

        query = self._search_config.finalize_query(query)
        return query

    def _handle_key_value(self, query, key, value, negated):
        if key == 'sort':
            return self._handle_sort(query, value, negated)
        elif key == 'special':
            return self._handle_special(query, value, negated)
        else:
            return self._handle_named(query, key, value, negated)

    def _handle_anonymous(self, query, criterion):
        if not self._search_config.anonymous_filter:
            raise errors.SearchError(
                'Anonymous tokens are not valid in this context.')
        return self._search_config.anonymous_filter(query, criterion)

    def _handle_named(self, query, key, value, negated):
        if key.endswith('-min'):
            key = key[:-4]
            value += '..'
        elif key.endswith('-max'):
            key = key[:-4]
            value = '..' + value
        criterion = self._create_criterion(value, negated)
        if key in self._search_config.named_filters:
            return self._search_config.named_filters[key](query, criterion)
        raise errors.SearchError(
            'Unknown named token: %r. Available named tokens: %r.' % (
                key, list(self._search_config.named_filters.keys())))

    def _handle_special(self, query, value, negated):
        if value in self._search_config.special_filters:
            return self._search_config.special_filters[value](
                query, value, negated)
        raise errors.SearchError(
            'Unknown special token: %r. Available special tokens: %r.' % (
                value, list(self._search_config.special_filters.keys())))

    def _handle_sort(self, query, value, negated):
        if value.count(',') == 0:
            dir_str = None
        elif value.count(',') == 1:
            value, dir_str = value.split(',')
        else:
            raise errors.SearchError('Too many commas in sort style token.')

        try:
            column, default_sort = self._search_config.sort_columns[value]
        except KeyError:
            raise errors.SearchError(
                'Unknown sort style: %r. Available sort styles: %r.' % (
                    value, list(self._search_config.sort_columns.keys())))

        sort_asc = self._search_config.SORT_ASC
        sort_desc = self._search_config.SORT_DESC

        try:
            sort_map = {
                'asc': sort_asc,
                'desc': sort_desc,
                '': default_sort,
                None: default_sort,
            }
            sort = sort_map[dir_str]
        except KeyError:
            raise errors.SearchError('Unknown search direction: %r.' % dir_str)

        if negated and sort:
            sort = -sort

        transform_map = {
            sort_asc: lambda input: input.asc(),
            sort_desc: lambda input: input.desc(),
            None: lambda input: input,
        }
        transform = transform_map[sort]
        return query.order_by(transform(column))

    def _create_criterion(self, value, negated):
        if '..' in value:
            low, high = value.split('..', 1)
            if not low and not high:
                raise errors.SearchError('Empty ranged value')
            return criteria.RangedSearchCriterion(value, negated, low, high)
        if ',' in value:
            return criteria.ArraySearchCriterion(
                value, negated, value.split(','))
        return criteria.PlainSearchCriterion(value, negated, value)
