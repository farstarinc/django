from django.contrib.admin.filterspecs import FilterSpec, FieldFilterSpec
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.util import quote, get_fields_from_path
from django.core.exceptions import SuspiciousOperation
from django.core.paginator import Paginator, InvalidPage
from django.db import models
from django.utils.datastructures import SortedDict
from django.utils.encoding import force_unicode, smart_str
from django.utils.translation import ugettext
from django.utils.http import urlencode
import operator

# The system will display a "Show all" link on the change list only if the
# total result count is less than or equal to this setting.
MAX_SHOW_ALL_ALLOWED = 200

# Changelist settings
ALL_VAR = 'all'
ORDER_VAR = 'o'
ORDER_TYPE_VAR = 'ot'
PAGE_VAR = 'p'
SEARCH_VAR = 'q'
TO_FIELD_VAR = 't'
IS_POPUP_VAR = 'pop'
ERROR_FLAG = 'e'

# Text to display within change-list table cells if the value is blank.
EMPTY_CHANGELIST_VALUE = '(None)'

class ChangeList(object):
    def __init__(self, request, model, list_display, list_display_links, list_filter, date_hierarchy, search_fields, list_select_related, list_per_page, list_editable, model_admin):
        self.model = model
        self.opts = model._meta
        self.lookup_opts = self.opts
        self.root_query_set = model_admin.queryset(request)
        self.list_display = list_display
        self.list_display_links = list_display_links
        self.list_filter = list_filter
        self.date_hierarchy = date_hierarchy
        self.search_fields = search_fields
        self.list_select_related = list_select_related
        self.list_per_page = list_per_page
        self.model_admin = model_admin

        # Get search parameters from the query string.
        try:
            self.page_num = int(request.GET.get(PAGE_VAR, 0))
        except ValueError:
            self.page_num = 0
        self.show_all = ALL_VAR in request.GET
        self.is_popup = IS_POPUP_VAR in request.GET
        self.to_field = request.GET.get(TO_FIELD_VAR)
        self.params = dict(request.GET.items())
        if PAGE_VAR in self.params:
            del self.params[PAGE_VAR]
        if TO_FIELD_VAR in self.params:
            del self.params[TO_FIELD_VAR]
        if ERROR_FLAG in self.params:
            del self.params[ERROR_FLAG]

        if self.is_popup:
            self.list_editable = ()
        else:
            self.list_editable = list_editable
        self.filter_specs, self.has_filters = self.get_filters(request)
        self.ordering = self.get_ordering()
        self.query = request.GET.get(SEARCH_VAR, '')
        self.query_set = self.get_query_set()
        self.get_results(request)
        self.title = (self.is_popup and ugettext('Select %s') % force_unicode(self.opts.verbose_name) or ugettext('Select %s to change') % force_unicode(self.opts.verbose_name))
        self.pk_attname = self.lookup_opts.pk.attname

    def get_filters(self, request):
        filter_specs = []
        if self.list_filter:
            for item in self.list_filter:
                if callable(item):
                    spec = item(request, self.params, self.model, self.model_admin)
                else:
                    field_path = None
                    try:
                        field, factory = item
                    except (TypeError, ValueError):
                        field, factory = item, FieldFilterSpec.create
                    if not isinstance(field, models.Field):
                        field_path = field
                        field = get_fields_from_path(self.model, field_path)[-1]
                    spec = factory(field, request, self.params, self.model, 
                            self.model_admin, field_path=field_path)
                        
                if spec and spec.has_output():
                    filter_specs.append(spec)
        return filter_specs, bool(filter_specs)

    def get_query_string(self, new_params=None, remove=None):
        if new_params is None: new_params = {}
        if remove is None: remove = []
        p = self.params.copy()
        for r in remove:
            for k in p.keys():
                if k.startswith(r):
                    del p[k]
        for k, v in new_params.items():
            if v is None:
                if k in p:
                    del p[k]
            else:
                p[k] = v
        return '?%s' % urlencode(p)

    def get_results(self, request):
        paginator = self.model_admin.get_paginator(request, self.query_set, self.list_per_page)
        # Get the number of objects, with admin filters applied.
        result_count = paginator.count

        # Get the total number of objects, with no admin filters applied.
        # Perform a slight optimization: Check to see whether any filters were
        # given. If not, use paginator.hits to calculate the number of objects,
        # because we've already done paginator.hits and the value is cached.
        if not self.query_set.query.where:
            full_result_count = result_count
        else:
            full_result_count = self.root_query_set.count()

        can_show_all = result_count <= MAX_SHOW_ALL_ALLOWED
        multi_page = result_count > self.list_per_page

        # Get the list of objects to display on this page.
        if (self.show_all and can_show_all) or not multi_page:
            result_list = self.query_set._clone()
        else:
            try:
                result_list = paginator.page(self.page_num+1).object_list
            except InvalidPage:
                raise IncorrectLookupParameters

        self.result_count = result_count
        self.full_result_count = full_result_count
        self.result_list = result_list
        self.can_show_all = can_show_all
        self.multi_page = multi_page
        self.paginator = paginator

    def get_ordering(self):
        lookup_opts, params = self.lookup_opts, self.params
        # For ordering, first check the "ordering" parameter in the admin
        # options, then check the object's default ordering. If neither of
        # those exist, order descending by ID by default. Finally, look for
        # manually-specified ordering from the query string.
        ordering = self.model_admin.ordering or lookup_opts.ordering or ['-' + lookup_opts.pk.name]
        
        if ORDER_VAR in params:
            # Clear ordering and used params
            ordering = []
            order_params = params[ORDER_VAR].split(',')
            for p in order_params:
                try:
                    none, pfx, idx = p.rpartition('-')
                    field_name = self.list_display[int(idx)]
                    try:
                        f = lookup_opts.get_field(field_name)
                    except models.FieldDoesNotExist:
                        # See whether field_name is a name of a non-field
                        # that allows sorting.
                        try:
                            if callable(field_name):
                                attr = field_name
                            elif hasattr(self.model_admin, field_name):
                                attr = getattr(self.model_admin, field_name)
                            else:
                                attr = getattr(self.model, field_name)
                            field_name = attr.admin_order_field
                        except AttributeError:
                            continue # Invalid ordering field. Skip it.
                    else:
                        field_name = f.name

                    ordering.append(pfx + field_name)

                except (IndexError, ValueError):
                    pass # Invalid ordering specified. Skip it.

        return ordering
    
    def get_ordering_fields(self):
        # Returns a SortedDict of ordering fields and asc/desc
        ordering_fields = SortedDict()
        for o in self.ordering:
            none, t, f = o.rpartition('-')
            ordering_fields[f] = t == '-' and 'desc' or 'asc'
        return ordering_fields

    def get_query_set(self):
        qs = self.root_query_set
        lookup_params = self.params.copy() # a dictionary of the query string
        for i in (ALL_VAR, ORDER_VAR, ORDER_TYPE_VAR, SEARCH_VAR, IS_POPUP_VAR):
            if i in lookup_params:
                del lookup_params[i]
        key = ''
        for key, value in lookup_params.items():
            if not isinstance(key, str):
                # 'key' will be used as a keyword argument later, so Python
                # requires it to be a string.
                del lookup_params[key]
                lookup_params[smart_str(key)] = value

            # if key ends with __in, split parameter into separate values
            if key.endswith('__in'):
                lookup_params[key] = value.split(',')

            # if key ends with __isnull, special case '' and false
            if key.endswith('__isnull'):
                if value.lower() in ('', 'false'):
                    lookup_params[key] = False
                else:
                    lookup_params[key] = True
                
        # Let every filter spec modify the qs and params to its liking
        for filter_spec in self.filter_specs:
            new_qs = filter_spec.get_query_set(self, qs)
            if new_qs is not None and new_qs is not False:
                qs = new_qs
                # Only consume params if we got a new queryset
                for param in filter_spec.consumed_params():
                    try:
                        del lookup_params[param]
                    except KeyError:
                        pass

            if not self.model_admin.lookup_allowed(key):
                raise SuspiciousOperation(
                    "Filtering by %s not allowed" % key
                )

        # Apply lookup parameters from the query string.
        try:
            qs = qs.filter(**lookup_params)
        # Naked except! Because we don't have any other way of validating "params".
        # They might be invalid if the keyword arguments are incorrect, or if the
        # values are not in the correct type, so we might get FieldError, ValueError,
        # ValicationError, or ? from a custom field that raises yet something else
        # when handed impossible data.
        except:
            raise IncorrectLookupParameters

        # Use select_related() if one of the list_display options is a field
        # with a relationship and the provided queryset doesn't already have
        # select_related defined.
        if not qs.query.select_related:
            if self.list_select_related:
                qs = qs.select_related()
            else:
                for field_name in self.list_display:
                    try:
                        f = self.lookup_opts.get_field(field_name)
                    except models.FieldDoesNotExist:
                        pass
                    else:
                        if isinstance(f.rel, models.ManyToOneRel):
                            qs = qs.select_related()
                            break

        # Set ordering.
        if self.ordering:
            qs = qs.order_by(*self.ordering)

        # Apply keyword searches.
        def construct_search(field_name):
            if field_name.startswith('^'):
                return "%s__istartswith" % field_name[1:]
            elif field_name.startswith('='):
                return "%s__iexact" % field_name[1:]
            elif field_name.startswith('@'):
                return "%s__search" % field_name[1:]
            else:
                return "%s__icontains" % field_name

        if self.search_fields and self.query:
            for bit in self.query.split():
                or_queries = [models.Q(**{construct_search(str(field_name)): bit}) for field_name in self.search_fields]
                qs = qs.filter(reduce(operator.or_, or_queries))
            for field_name in self.search_fields:
                if '__' in field_name:
                    qs = qs.distinct()
                    break

        return qs

    def url_for_result(self, result):
        return "%s/" % quote(getattr(result, self.pk_attname))
