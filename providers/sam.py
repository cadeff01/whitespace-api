import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import requests
from flask import Blueprint, current_app, jsonify, request
from werkzeug.datastructures import MultiDict

from providers.errors import APIError
from providers.secrets import sam_api_key

sam_api = Blueprint('sam', __name__)
SEARCH_URL = 'https://api.sam.gov/opportunities/v2/search'
FILTERS = {
    'title': 'title',
    'notice_id': 'noticeid',
    'solicitation_number': 'solnum',
    'notice_type': 'ptype',
    'naics_code': 'ncode',
    'set_aside': 'typeOfSetAside',
    'state': 'state',
}
FIELDS = {
    'notice_id': 'noticeId',
    'title': 'title',
    'solicitation_number': 'solicitationNumber',
    'agency': 'fullParentPathName',
    'posted_date': 'postedDate',
    'response_deadline': 'responseDeadLine',
    'notice_type': 'type',
    'naics_code': 'naicsCode',
    'set_aside': 'typeOfSetAside',
    'active': 'active',
}


@dataclass(frozen=True)
class SearchQuery:
    filters: dict[str, str]
    page: int
    page_size: int


def invalid(message: str) -> APIError:
    return APIError(400, 'invalid_request', message)


def search_parameters(args: MultiDict[str, str]) -> SearchQuery:
    allowed = {'posted_from', 'posted_to', 'page', 'page_size'} | FILTERS.keys()
    if any(key not in allowed for key in args):
        raise invalid('Unknown query parameter. See /openapi.json for supported filters.')
    if any(len(args.getlist(key)) != 1 for key in args):
        raise invalid('Query parameters must not be repeated.')
    dates = []
    for key in ('posted_from', 'posted_to'):
        value = args.get(key, '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise invalid(f'{key} is required in YYYY-MM-DD format.')
        try:
            dates.append(date.fromisoformat(value))
        except ValueError:
            raise invalid(f'{key} must be a valid calendar date.') from None
    start, end = dates
    if (end < start or end.year > start.year + 1
            or (end.year == start.year + 1
                and (end.month, end.day) > (start.month, start.day))):
        raise invalid('posted_to must be on or after posted_from and within one year.')
    params = {
        'postedFrom': f'{start.month:02d}/{start.day:02d}/{start.year:04d}',
        'postedTo': f'{end.month:02d}/{end.day:02d}/{end.year:04d}',
    }
    pagination = {}
    for key, default, minimum, maximum in (
        ('page', '0', 0, 2147483647),
        ('page_size', '20', 1, 100),
    ):
        value = args.get(key, default)
        if not re.fullmatch(r'[0-9]{1,10}', value) or not minimum <= int(value) <= maximum:
            raise invalid(f'{key} must be an integer between {minimum} and {maximum}.')
        pagination[key] = int(value)
    for key, upstream in FILTERS.items():
        if key not in args:
            continue
        value = args[key].strip()
        if not value or len(value) > 200:
            raise invalid(f'{key} must contain between 1 and 200 characters.')
        if key == 'notice_type' and value not in {'u', 'p', 'a', 'r', 's', 'o', 'g', 'k', 'i'}:
            raise invalid('notice_type is not a supported SAM.gov procurement type.')
        if key == 'naics_code' and not re.fullmatch(r'[0-9]{1,6}', value):
            raise invalid('naics_code must contain 1 to 6 digits.')
        if key == 'state' and not re.fullmatch(r'[A-Za-z]{2}', value):
            raise invalid('state must be a two-letter code.')
        params[upstream] = value.upper() if key == 'state' else value
    return SearchQuery(params, pagination['page'], pagination['page_size'])


def bad_response() -> APIError:
    return APIError(502, 'invalid_upstream_response', 'SAM.gov returned an unexpected response.')


def search_opportunities(query: SearchQuery, api_key: str) -> dict:
    try:
        # SAM.gov requires its credential in the query string. Do not follow redirects
        # or log response/exception text, which may contain the authenticated URL.
        response = requests.get(
            SEARCH_URL, params={**query.filters, 'offset': query.page,
                                'limit': query.page_size, 'api_key': api_key},
            timeout=(5, 25), allow_redirects=False,
        )
    except requests.Timeout:
        raise APIError(504, 'upstream_timeout', 'SAM.gov did not respond in time.') from None
    except requests.RequestException:
        raise APIError(502, 'upstream_unavailable', 'SAM.gov could not be reached.') from None
    try:
        if response.status_code == 429:
            raise APIError(429, 'upstream_rate_limited', 'SAM.gov quota exceeded. Try again later.')
        if response.status_code in (401, 403):
            raise APIError(502, 'upstream_authentication_failed', 'SAM.gov rejected the configured credential.')
        if response.status_code != 200:
            raise APIError(502, 'upstream_error', 'SAM.gov could not complete the search.')
        try:
            data = response.json()
        except ValueError:
            raise bad_response() from None
    finally:
        response.close()
    if not isinstance(data, dict):
        raise bad_response()
    total, records = data.get('totalRecords'), data.get('opportunitiesData')
    if (type(total) is not int or total < 0 or not isinstance(records, list)
            or len(records) > query.page_size):
        raise bad_response()
    opportunities = []
    for record in records:
        if not isinstance(record, dict):
            raise bad_response()
        item = {}
        for field, upstream in FIELDS.items():
            value = record.get(upstream)
            if value is not None and not isinstance(value, str):
                raise bad_response()
            item[field] = value
        if not item['notice_id'] or not item['title']:
            raise bad_response()
        # Build a public URL rather than forwarding upstream links that can contain keys.
        item['url'] = f"https://sam.gov/opp/{quote(item['notice_id'], safe='')}/view"
        opportunities.append(item)
    page, size = query.page, query.page_size
    return {
        'source': 'sam.gov', 'total_records': total, 'page': page, 'page_size': size,
        'next_page': page + 1 if records and (page + 1) * size < total else None,
        'opportunities': opportunities,
    }


@sam_api.get('/opportunities')
def opportunities():
    params = search_parameters(request.args)
    api_key = sam_api_key(
        current_app.config['AZURE_KEY_VAULT_ENDPOINT'],
        current_app.config['SAM_API_KEY_SECRET_NAME'],
    )
    return jsonify(search_opportunities(params, api_key))
