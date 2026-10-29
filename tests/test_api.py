import os
import unittest
from unittest.mock import Mock, patch

import requests
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
from swagger_spec_validator.validator20 import validate_spec
from werkzeug.wrappers import Response

from app import app
from providers.errors import APIError
from providers.secrets import sam_api_key, secret_client


def json_body(response: Response) -> dict:
    value = response.get_json()
    assert isinstance(value, dict)
    return value


class APITests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = app.config.copy()
        self.addCleanup(app.config.update, self.config)
        app.config.update(TESTING=True, LOCAL_DEVELOPMENT=True)
        self.client = app.test_client()
        self.query = {'posted_from': '2026-01-01', 'posted_to': '2026-10-29'}
        self.secret = patch('providers.sam.sam_api_key', return_value='test-secret-not-a-real-key')
        self.get_secret = self.secret.start()
        self.addCleanup(self.secret.stop)
        self.http = patch('providers.sam.requests.get')
        self.get = self.http.start()
        self.addCleanup(self.http.stop)
        self.response = Mock(status_code=200)
        self.response.json.return_value = {
            'totalRecords': 42,
            'opportunitiesData': [{
                'noticeId': 'notice-123', 'title': 'Cloud services',
                'solicitationNumber': 'SOL-1', 'fullParentPathName': 'Department.Agency',
                'postedDate': '2026-10-01', 'responseDeadLine': '2026-11-15T12:00:00',
                'type': 'Solicitation', 'naicsCode': '541512',
                'typeOfSetAside': 'SBA', 'active': 'Yes',
                'description': 'https://api.sam.gov/description?api_key=never-forward',
                'uiLink': 'https://example.invalid/?api_key=never-forward',
            }],
        }
        self.get.return_value = self.response

    def search(self, query=None, headers=None):
        return self.client.get('/api/sam/opportunities',
                               query_string=self.query if query is None else query,
                               headers=headers)

    def test_normalized_search_and_upstream_mapping(self):
        self.query.update(title='cloud', page='1', page_size='20', state='va',
                          naics_code='541512', notice_type='o', set_aside='SBA',
                          notice_id='notice-123', solicitation_number='SOL-1')
        response = self.search()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(
            {key: data[key] for key in ('source', 'total_records', 'page', 'page_size', 'next_page')},
            {'source': 'sam.gov', 'total_records': 42, 'page': 1, 'page_size': 20, 'next_page': 2},
        )
        self.assertEqual(data['opportunities'][0], {
            'notice_id': 'notice-123', 'title': 'Cloud services',
            'solicitation_number': 'SOL-1', 'agency': 'Department.Agency',
            'posted_date': '2026-10-01', 'response_deadline': '2026-11-15T12:00:00',
            'notice_type': 'Solicitation', 'naics_code': '541512',
            'set_aside': 'SBA', 'active': 'Yes', 'url': 'https://sam.gov/opp/notice-123/view',
        })
        self.get_secret.assert_called_once_with('https://whitespacekv.vault.azure.net/', 'sam-gov-api-key')
        self.get.assert_called_once_with(
            'https://api.sam.gov/opportunities/v2/search',
            params={'postedFrom': '01/01/2026', 'postedTo': '10/29/2026',
                    'api_key': 'test-secret-not-a-real-key', 'offset': 1, 'limit': 20,
                    'title': 'cloud', 'state': 'VA', 'ncode': '541512', 'ptype': 'o',
                    'typeOfSetAside': 'SBA', 'noticeid': 'notice-123', 'solnum': 'SOL-1'},
            timeout=(5, 25), allow_redirects=False,
        )
        self.response.close.assert_called_once()
        self.assertNotIn('api_key', response.get_data(as_text=True))
        self.assertNotIn('test-secret', response.get_data(as_text=True))

    def test_pagination_boundaries(self):
        for page, size, total, records, expected_next in (
            (0, 1, 2, [{'noticeId': '1', 'title': 'one'}], 1),
            (1, 1, 2, [{'noticeId': '2', 'title': 'two'}], None),
            (3, 100, 0, [], None),
        ):
            with self.subTest(page=page, size=size):
                self.response.json.return_value = {'totalRecords': total, 'opportunitiesData': records}
                response = self.search({**self.query, 'page': str(page), 'page_size': str(size)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json_body(response)['next_page'], expected_next)

    def test_invalid_queries_do_not_access_secrets_or_sam(self):
        changes = [
            {'posted_from': ''}, {'posted_to': ''}, {'posted_from': '01/01/2026'},
            {'posted_from': '2026-02-30'}, {'posted_from': '2026-1-01'},
            {'posted_to': '2025-01-01'}, {'posted_to': '2027-01-02'},
            {'page': '-1'}, {'page': '1.0'}, {'page': '2147483648'},
            {'page_size': '0'}, {'page_size': '101'}, {'page_size': ''},
            {'title': ' '}, {'title': 'x' * 201}, {'notice_type': 'f'},
            {'naics_code': '1234567'}, {'naics_code': 'abc'}, {'state': 'Virginia'},
            {'api_key': 'caller-key'}, {'url': 'https://example.com'},
        ]
        for change in changes:
            with self.subTest(change=change):
                response = self.search({**self.query, **change})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(json_body(response)['error']['code'], 'invalid_request')
        self.assertEqual(self.search({}).status_code, 400)
        repeated = list(self.query.items()) + [('title', 'a'), ('title', 'b')]
        self.assertEqual(self.search(repeated).status_code, 400)
        self.get_secret.assert_not_called()
        self.get.assert_not_called()

    def test_calendar_year_and_leap_day_ranges(self):
        for start, end, status in (
            ('2024-02-29', '2025-02-28', 200),
            ('2024-02-29', '2025-03-01', 400),
            ('2023-03-01', '2024-03-01', 200),
            ('2026-01-01', '2027-01-01', 200),
            ('9999-12-31', '9999-12-31', 200),
        ):
            with self.subTest(start=start, end=end):
                self.assertEqual(self.search({'posted_from': start, 'posted_to': end}).status_code, status)

    def test_authentication_fails_closed(self):
        app.config['LOCAL_DEVELOPMENT'] = False
        self.assertEqual(self.search().status_code, 401)
        headers = {'X-MS-CLIENT-PRINCIPAL-ID': 'caller', 'X-MS-CLIENT-PRINCIPAL-IDP': 'aad'}
        self.assertEqual(self.search(headers=headers).status_code, 401)
        with patch.dict(os.environ, {'WEBSITE_SITE_NAME': 'test-app'}):
            app.config['LOCAL_DEVELOPMENT'] = True
            self.assertEqual(self.search().status_code, 401)
            self.assertEqual(self.search(headers={**headers, 'X-MS-CLIENT-PRINCIPAL-IDP': 'github'}).status_code, 401)
            self.assertEqual(self.search(headers=headers).status_code, 200)
        self.get_secret.assert_called_once()

    def test_upstream_http_errors_are_sanitized(self):
        for upstream, expected, code in (
            (401, 502, 'upstream_authentication_failed'),
            (403, 502, 'upstream_authentication_failed'),
            (429, 429, 'upstream_rate_limited'),
            (500, 502, 'upstream_error'),
            (302, 502, 'upstream_error'),
        ):
            with self.subTest(upstream=upstream):
                self.response.status_code = upstream
                self.response.text = 'api_key=test-secret'
                with self.assertLogs(app.logger, level='WARNING') as logs:
                    response = self.search()
                self.assertEqual(response.status_code, expected)
                self.assertEqual(json_body(response)['error']['code'], code)
                self.assertNotIn('test-secret', response.get_data(as_text=True) + str(logs.output))

    def test_network_errors_are_sanitized(self):
        for exception, status, code in (
            (requests.Timeout('api_key=test-secret'), 504, 'upstream_timeout'),
            (requests.ConnectionError('api_key=test-secret'), 502, 'upstream_unavailable'),
        ):
            with self.subTest(code=code):
                self.get.side_effect = exception
                with self.assertLogs(app.logger, level='WARNING') as logs:
                    response = self.search()
                self.assertEqual(response.status_code, status)
                self.assertEqual(json_body(response)['error']['code'], code)
                self.assertNotIn('test-secret', response.get_data(as_text=True) + str(logs.output))

    def test_malformed_upstream_data(self):
        for payload in (
            None, [], {}, {'totalRecords': True, 'opportunitiesData': []},
            {'totalRecords': -1, 'opportunitiesData': []},
            {'totalRecords': 1, 'opportunitiesData': None},
            {'totalRecords': 1, 'opportunitiesData': ['bad']},
            {'totalRecords': 1, 'opportunitiesData': [{'title': 'no id'}]},
            {'totalRecords': 1, 'opportunitiesData': [{'noticeId': '1', 'title': 1}]},
            {'totalRecords': 21, 'opportunitiesData': [{'noticeId': '1', 'title': 'x'}] * 21},
        ):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                self.assertEqual(self.search().status_code, 502)
        self.response.json.side_effect = ValueError('bad json with api_key=test-secret')
        self.assertEqual(json_body(self.search())['error']['code'], 'invalid_upstream_response')
        self.assertEqual(self.response.close.call_count, 11)

    def test_key_vault_error_prevents_upstream_call(self):
        self.get_secret.side_effect = APIError(503, 'secret_unavailable', 'Credential unavailable.')
        self.assertEqual(self.search().status_code, 503)
        self.get.assert_not_called()

    def test_health_schema_and_existing_routes(self):
        app.config['LOCAL_DEVELOPMENT'] = False
        self.assertEqual(json_body(self.client.get('/health')), {'status': 'ok'})
        with self.client.get('/openapi.json') as response:
            schema = json_body(response)
        validate_spec(schema)
        self.assertEqual(schema['swagger'], '2.0')
        operation = schema['paths']['/api/sam/opportunities']['get']
        self.assertEqual(operation['operationId'], 'SearchSamOpportunities')
        self.assertNotIn('api_key', [param['name'] for param in operation['parameters']])
        for definition in schema['definitions'].values():
            self.assertEqual(definition['type'], 'object')
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.post('/hello', data={'name': 'Agent'}).status_code, 200)
        self.assertEqual(self.client.post('/hello', data={}).status_code, 302)
        self.get_secret.assert_not_called()

    def test_api_http_errors_are_json(self):
        self.assertEqual(self.client.get('/api/sam/missing').status_code, 404)
        response = self.client.post('/api/sam/opportunities')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(json_body(response)['error']['code'], 'method_not_allowed')


class KeyVaultTests(unittest.TestCase):
    def tearDown(self):
        secret_client.cache_clear()

    @patch('providers.secrets.secret_client')
    def test_secret_retrieval_and_rotation(self, factory):
        factory.return_value.get_secret.side_effect = [Mock(value='first'), Mock(value='rotated')]
        self.assertEqual(sam_api_key('https://whitespacekv.vault.azure.net/', 'sam-gov-api-key'), 'first')
        self.assertEqual(sam_api_key('https://whitespacekv.vault.azure.net/', 'sam-gov-api-key'), 'rotated')
        factory.return_value.get_secret.assert_called_with('sam-gov-api-key')

    @patch('providers.secrets.secret_client')
    def test_secret_errors_are_sanitized(self, factory):
        for error in (ClientAuthenticationError('sensitive'), ResourceNotFoundError('sensitive')):
            with self.subTest(error=type(error).__name__):
                factory.return_value.get_secret.side_effect = error
                with self.assertRaises(APIError) as caught:
                    sam_api_key('https://whitespacekv.vault.azure.net/', 'sam-gov-api-key')
                self.assertEqual(caught.exception.status, 503)
                self.assertNotIn('sensitive', str(caught.exception))
        factory.return_value.get_secret.side_effect = None
        for value in (None, '', '  '):
            factory.return_value.get_secret.return_value = Mock(value=value)
            with self.assertRaises(APIError):
                sam_api_key('https://whitespacekv.vault.azure.net/', 'sam-gov-api-key')

    @patch('providers.secrets.secret_client')
    def test_invalid_vault_configuration(self, factory):
        for url in ('', 'http://whitespacekv.vault.azure.net/', 'https://evil.example/',
                    'https://whitespacekv.vault.azure.net:bad/',
                    'https://whitespacekv.vault.azure.net/path', 'https://[invalid'):
            with self.subTest(url=url), self.assertRaises(APIError):
                sam_api_key(url, 'sam-gov-api-key')
        with self.assertRaises(APIError):
            sam_api_key('https://whitespacekv.vault.azure.net/', 'invalid name')
        factory.assert_not_called()

    @patch('providers.secrets.SecretClient')
    @patch('providers.secrets.DefaultAzureCredential')
    @patch('providers.secrets.ManagedIdentityCredential')
    def test_azure_uses_managed_identity_and_local_uses_default_credential(self, managed, default, client):
        with patch.dict(os.environ, {}, clear=True):
            secret_client('https://local.vault.azure.net/')
            default.assert_called_once_with(exclude_interactive_browser_credential=True)
            self.assertEqual(client.call_args.kwargs['credential'], default.return_value)
        with patch.dict(os.environ, {'WEBSITE_SITE_NAME': 'test-app'}):
            secret_client('https://production.vault.azure.net/')
            managed.assert_called_once_with()
            self.assertEqual(client.call_args.kwargs['credential'], managed.return_value)


if __name__ == '__main__':
    unittest.main()
