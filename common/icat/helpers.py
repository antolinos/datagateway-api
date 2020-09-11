from functools import wraps
import logging
from datetime import datetime, timedelta
from collections.abc import Iterable

from icat.entity import Entity, EntityList
from icat.query import Query
from icat.exception import ICATSessionError, ICATValidationError
from common.exceptions import (
    AuthenticationError,
    BadRequestError,
    MissingRecordError,
    PythonICATError,
)
from common.filter_order_handler import FilterOrderHandler
from common.constants import Constants
from common.icat.filters import (
    PythonICATLimitFilter,
    PythonICATWhereFilter,
    PythonICATSkipFilter,
    PythonICATOrderFilter,
)


log = logging.getLogger()


def requires_session_id(method):
    """
    Decorator for Python ICAT backend methods that looks out for session errors when
    using the API. The API call runs and an ICATSessionError may be raised due to an
    expired session, invalid session ID etc.
 
    :param method: The method for the backend operation
    :raises AuthenticationError: If a valid session_id is not provided with the request
    """

    @wraps(method)
    def wrapper_requires_session(*args, **kwargs):
        try:

            client = args[0].client
            # Find out if session has expired
            session_time = client.getRemainingMinutes()
            log.info("Session time: %d", session_time)
            if session_time < 0:
                raise AuthenticationError("Forbidden")
            else:
                return method(*args, **kwargs)
        except ICATSessionError:
            raise AuthenticationError("Forbidden")

    return wrapper_requires_session


def get_session_details_helper(client):
    """
    Retrieve details regarding the current session within `client`

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :return: Details of the user's session, ready to be converted into a JSON response
        body
    """
    # Remove rounding
    session_time_remaining = client.getRemainingMinutes()
    session_expiry_time = datetime.now() + timedelta(minutes=session_time_remaining)

    username = client.getUserName()

    return {
        "ID": client.sessionId,
        "EXPIREDATETIME": str(session_expiry_time),
        "USERNAME": username,
    }


def logout_icat_client(client):
    """
    Logout a user of the currently authenticated user within `client`

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    """

    client.logout()


def refresh_client_session(client):
    """
    Refresh the session of the currently authenticated user within `client`

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    """
    client.refresh()


class icat_query:
    def __init__(
        self, client, entity_name, conditions=None, aggregate=None, includes=None
    ):
        """
        Create a Query object within Python ICAT 

        :param client: ICAT client containing an authenticated user
        :type client: :class:`icat.client.Client`
        :param entity_name: Name of the entity to get data from
        :type entity_name: :class:`suds.sax.text.Text`
        :param conditions: Constraints used when an entity is queried
        :type conditions: :class:`dict`
        :param aggregate: Name of the aggregate function to apply. Operations such as
            counting the number of records. See `icat.query.setAggregate` for valid
            values.
        :type aggregate: :class:`str`
        :param includes: List of related entity names to add to the query so related
            entities (and their data) can be returned with the query result
        :type includes: :class:`str` or iterable of :class:`str`
        :return: Query object from Python ICAT
        :raises PythonICATError: If a ValueError is raised when creating a Query(), 500
            will be returned as a response
        """

        try:
            log.info("Creating ICATQuery for entity: %s", entity_name)
            self.query = Query(
                client,
                entity_name,
                conditions=conditions,
                aggregate=aggregate,
                includes=includes,
            )
        except ValueError:
            raise PythonICATError(
                "An issue has occurred while creating a Python ICAT Query object,"
                " suggesting an invalid argument"
            )

    def execute_query(self, client, return_json_formattable=False):
        """
        Execute a previously created ICAT Query object and return in the format
        specified by the return_json_formattable flag

        :param client: ICAT client containing an authenticated user
        :type client: :class:`icat.client.Client`
        :param return_json_formattable: Flag to determine whether the data from the 
            query should be returned as a list of data ready to be converted straight to
            JSON (i.e. if the data will be used as a response for an API call) or
            whether to leave the data in a Python ICAT format (i.e. if it's going to be
            manipulated at some point)
        :type return_json_formattable_data: :class:`bool`
        :return: Data (of type list) from the executed query
        :raises PythonICATError: If an error occurs during query execution
        """

        try:
            log.debug("Executing ICAT query")
            query_result = client.search(self.query)
        except ICATValidationError as e:
            raise PythonICATError(e)

        if self.query.aggregate == "DISTINCT":
            log.info("Extracting the distinct fields from query's conditions")
            distinct_filter_flag = True
            # Check query's conditions for the ones created by the distinct filter
            self.attribute_names = []

            for key, value in self.query.conditions.items():
                # Value can be a list if there's multiple WHERE filters for the same
                # attribute name within an ICAT query
                if isinstance(value, list):
                    for sub_value in value:
                        self.check_attribute_name_for_distinct(key, sub_value)
                elif isinstance(value, str):
                    self.check_attribute_name_for_distinct(key, value)
            log.debug(
                "Attribute names used in the distinct filter, as captured by the"
                " query's conditions %s",
                self.attribute_names,
            )
        else:
            distinct_filter_flag = False

        if return_json_formattable:
            log.info("Query results will be returned in a JSON format")
            data = []

            for result in query_result:
                distinct_result = {}
                dict_result = self.entity_to_dict(result, self.query.includes)

                for key, value in dict_result.items():
                    if distinct_filter_flag:
                        # Add only the required data as per request's distinct filter
                        # fields
                        if key in self.attribute_names:
                            distinct_result[key] = dict_result[key]

                if distinct_filter_flag:
                    data.append(distinct_result)
                else:
                    data.append(dict_result)
            return data
        else:
            log.info("Query results will be returned as ICAT entities")
            return query_result

    def check_attribute_name_for_distinct(self, key, value):
        """
        Check the attribute name to see if its associated value is used to signify the
        attribute is requested in a distinct filter and if so, append it to the list of
        attribute names

        :param key: Name of an attribute
        :type key: :class:`str`
        :param value: Expression that should be applied to the associated attribute
            e.g. "= 'Metadata'"
        :type value: :class:`str`
        """
        if value == Constants.PYTHON_ICAT_DISTNCT_CONDITION:
            self.attribute_names.append(key)

    def datetime_object_to_str(self, date_obj):
        """
        Convert a datetime object to a string so it can be outputted in JSON

        There's currently no reason to make this function static, but it could be useful
        in the future if a use case required this functionality.

        :param date_obj: Datetime object from data from an ICAT entity
        :type date_obj: :class:`datetime.datetime`
        :return: Datetime (of type string) in the agreed format
        """
        return date_obj.replace(tzinfo=None).strftime(Constants.ACCEPTED_DATE_FORMAT)

    def entity_to_dict(self, entity, includes):
        """
        This expands on Python ICAT's implementation of `icat.entity.Entity.as_dict()`
        to use set operators to create a version of the entity as a dictionary

        Most of this function is dedicated to recursing over included fields from a
        query, since this is functionality isn't part of Python ICAT's `as_dict()`. This
        function can be used when there are no include filters in the query/request
        however.

        :param entity: Python ICAT entity from an ICAT query
        :type entity: :class:`icat.entities.ENTITY` (implementation of
            :class:`icat.entity.Entity`) or :class:`icat.entity.EntityList`
        :param includes: Set of fields that have been included in the ICAT query. Where
            fields have a chain of relationships, they're a single element string
            separated by dots
        :type includes: :class:`set`
        :return: ICAT Data (of type dictionary) ready to be serialised to JSON
        """
        d = {}

        # Split up the fields separated by dots and flatten the resulting lists
        flat_includes = [m for n in (field.split(".") for field in includes) for m in n]

        # Verifying that `flat_includes` only has fields which are related to the entity
        include_set = (entity.InstRel | entity.InstMRel) & set(flat_includes)
        for key in entity.InstAttr | entity.MetaAttr | include_set:
            if key in flat_includes:
                target = getattr(entity, key)
                # Copy and remove don't return values so must be done separately
                includes_copy = flat_includes.copy()
                try:
                    includes_copy.remove(key)
                except ValueError:
                    log.warning(
                        "Key couldn't be found to remove from include list, this could"
                        " cause an issue further on in the request"
                    )
                if isinstance(target, Entity):
                    d[key] = self.entity_to_dict(target, includes_copy)
                # Related fields with one-many relationships are stored as EntityLists
                elif isinstance(target, EntityList):
                    d[key] = []
                    for e in target:
                        d[key].append(self.entity_to_dict(e, includes_copy))
            # Add actual piece of data to the dictionary
            else:
                entity_data = getattr(entity, key)
                # Convert datetime objects to strings ready to be outputted as JSON
                if isinstance(entity_data, datetime):
                    # Remove timezone data which isn't utilised in ICAT
                    entity_data = self.datetime_object_to_str(entity_data)
                d[key] = entity_data
        return d


def get_python_icat_entity_name(client, database_table_name):
    """
    From the database table name, this function returns the correctly cased entity name
    relating to the table name

    Due to the case sensitivity of Python ICAT, the table name must be compared with
    each of the valid entity names within Python ICAT to get the correctly cased entity
    name. This is done by putting everything to lowercase and comparing from there

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :param database_table_name: Table name (from icatdb) to be interacted with
    :type database_table_name: :class:`str`
    :return: Entity name (of type string) in the correct casing ready to be passed into
        Python ICAT
    :raises BadRequestError: If the entity cannot be found
    """

    lowercase_table_name = database_table_name.lower()
    entity_names = client.getEntityNames()
    python_icat_entity_name = None
    for entity_name in entity_names:
        lowercase_name = entity_name.lower()

        if lowercase_name == lowercase_table_name:
            python_icat_entity_name = entity_name

    # Raise a 400 if a valid entity cannot be found
    if python_icat_entity_name is None:
        raise BadRequestError(
            f"Bad request made, cannot find {database_table_name} entity within Python"
            " ICAT"
        )

    return python_icat_entity_name


def str_to_datetime_object(icat_attribute, data):
    """
    Where data is stored as dates in ICAT (which this function determines), convert 
    strings (i.e. user data from PATCH/POST requests) into datetime objects so they can
    be stored in ICAT

    Python 3.7+ has support for `datetime.fromisoformat()` which would be a more elegant
    solution to this conversion operation since dates are converted into ISO format
    within this file, however, the production instance of this API is typically built on
    Python 3.6, and it doesn't seem of enough value to mandate 3.7 for a single line of
    code

    :param icat_attribute: Attribute that will be updated with new data
    :type icat_attribute: Any valid data type that can be stored in Python ICAT
    :param data: Single data value from the request body
    :type data: Data type of the data as per user's request body
    :return: Date converted into a :class:`datetime` object
    :raises BadRequestError: If the date is entered in the incorrect format, as per
        `Constants.ACCEPTED_DATE_FORMAT`
    """

    try:
        data = datetime.strptime(data, Constants.ACCEPTED_DATE_FORMAT)
    except ValueError:
        raise BadRequestError(
            "Bad request made, the date entered is not in the correct format. Use the"
            f" {Constants.ACCEPTED_DATE_FORMAT} format to submit dates to the API"
        )

    return data


def update_attributes(old_entity, new_entity):
    """
    Updates the attribute(s) of a given object which is a record of an entity from
    Python ICAT

    :param old_entity: An existing entity record from Python ICAT
    :type object: :class:`icat.entities.ENTITY` (implementation of
        :class:`icat.entity.Entity`)
    :param new_entity: Dictionary containing the new data to be modified
    :type new_entity: :class:`dict`
    :raises BadRequestError: If the attribute cannot be found, or if it cannot be edited
        - typically if Python ICAT doesn't allow an attribute to be edited (e.g. modId &
        modTime)
    """
    for key in new_entity:
        try:
            original_data_attribute = getattr(old_entity, key)
            if isinstance(original_data_attribute, datetime):
                new_entity[key] = str_to_datetime_object(
                    original_data_attribute, new_entity[key]
                )
        except AttributeError:
            raise BadRequestError(
                f"Bad request made, cannot find attribute '{key}' within the"
                f"{old_entity.BeanName} entity"
            )

        try:
            setattr(old_entity, key, new_entity[key])
        except AttributeError:
            raise BadRequestError(
                f"Bad request made, cannot modify attribute '{key}' within the"
                f" {old_entity.BeanName} entity"
            )

    try:
        old_entity.update()
    except ICATValidationError as e:
        raise PythonICATError(e)


def get_entity_by_id(client, table_name, id_, return_json_formattable_data):
    """
    Gets a record of a given ID from the specified entity

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :param table_name: Table name to extract which entity to use
    :type table_name: :class:`str`
    :param id_: ID number of the entity to retrieve
    :type id_: :class:`int`
    :param return_json_formattable_data: Flag to determine whether the data should be 
        returned as a list of data ready to be converted straight to JSON (i.e. if the
        data will be used as a response for an API call) or whether to leave the data in
        a Python ICAT format
    :type return_json_formattable_data: :class:`bool`
    :return: The record of the specified ID from the given entity
    :raises: MissingRecordError: If Python ICAT cannot find a record of the specified ID
    """

    selected_entity_name = get_python_icat_entity_name(client, table_name)
    # Set query condition for the selected ID
    id_condition = PythonICATWhereFilter.create_condition("id", "=", id_)

    id_query = icat_query(
        client, selected_entity_name, conditions=id_condition, includes="1"
    )
    entity_by_id_data = id_query.execute_query(client, return_json_formattable_data)

    if not entity_by_id_data:
        # Cannot find any data matching the given ID
        raise MissingRecordError("No result found")
    else:
        return entity_by_id_data[0]


def delete_entity_by_id(client, table_name, id_):
    """
    Deletes a record of a given ID of the specified entity

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :param table_name: Table name to extract which entity to delete
    :type table_name: :class:`str`
    :param id_: ID number of the entity to delete
    :type id_: :class:`int`
    """

    entity_id_data = get_entity_by_id(client, table_name, id_, False)
    client.delete(entity_id_data)


def update_entity_by_id(client, table_name, id_, new_data):
    """
    Gets a record of a given ID of the specified entity

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :param table_name: Table name to extract which entity to use
    :type table_name: :class:`str`
    :param id_: ID number of the entity to retrieve
    :type id_: :class:`int`
    :param new_data: JSON from request body providing new data to update the record with
        the specified ID
    :return: The updated record of the specified ID from the given entity
    """

    entity_id_data = get_entity_by_id(client, table_name, id_, False)
    # There will only ever be one record associated with a single ID - if a record with
    # the specified ID cannot be found, it'll be picked up by the MissingRecordError in
    # get_entity_by_id()
    update_attributes(entity_id_data, new_data)

    # The record is re-obtained from Python ICAT (rather than using entity_id_data) to
    # show to the user whether the change has actually been applied
    return get_entity_by_id(client, table_name, id_, True)


def get_entity_with_filters(client, table_name, filters):
    """
    Gets all the records of a given entity, based on the filters provided in the request

    :param client: ICAT client containing an authenticated user
    :type client: :class:`icat.client.Client`
    :param table_name: Table name to extract which entity to use
    :type table_name: :class:`str`
    :param filters: The list of filters to be applied to the request
    :type filters: List of specific implementations :class:`QueryFilter`
    :return: The list of records of the given entity, using the filters to restrict the
        result of the query
    """

    selected_entity_name = get_python_icat_entity_name(client, table_name)
    query = icat_query(client, selected_entity_name)

    filter_handler = FilterOrderHandler()
    filter_handler.add_filters(filters)
    merge_limit_skip_filters(filter_handler)
    clear_order_filters(filter_handler.filters)
    filter_handler.apply_filters(query.query)

    data = query.execute_query(client, True)

    if not data:
        raise MissingRecordError("No results found")
    else:
        return data


def merge_limit_skip_filters(filter_handler):
    """
    When there are both limit and skip filters in a request, merge them into the limit
    filter and remove the skip filter from `filter_handler`

    :param filter_handler: The filter handler to apply the filters
    :param filters: The filters to be applied
    """

    if any(
        isinstance(filter, PythonICATSkipFilter) for filter in filter_handler.filters
    ) and any(
        isinstance(filter, PythonICATLimitFilter) for filter in filter_handler.filters
    ):
        # Merge skip and limit filter into a single limit filter
        for filter in filter_handler.filters:
            if isinstance(filter, PythonICATSkipFilter):
                skip_filter = filter
                request_skip_value = filter.skip_value

            if isinstance(filter, PythonICATLimitFilter):
                limit_filter = filter

        if skip_filter and limit_filter:
            log.info("Merging skip filter with limit filter")
            limit_filter.skip_value = skip_filter.skip_value
            log.info("Removing skip filter from list of filters")
            filter_handler.remove_filter(skip_filter)
            log.debug("Filters: %s", filter_handler.filters)


def clear_order_filters(filters):
    """
    Checks if any order filters have been added to the request and resets the variable
    used to manage which attribute(s) to use for sorting results.
    
    A reset is required because Python ICAT overwrites (as opposed to appending to it)
    the query's order list every time one is added to the query.

    :param filters: The list of filters to be applied to the request
    :type filters: List of specific implementations :class:`QueryFilter`
    """

    if any(isinstance(filter, PythonICATOrderFilter) for filter in filters):
        PythonICATOrderFilter.result_order = []
