import time
import json
import hmac
import base64
import logging
import hashlib
import requests
from urllib.parse import urljoin
from datetime import datetime, timezone


class MicrosoftSentinelConnector:
    def __init__(self, log_analytics_uri, workspace_id, shared_key):
        self.log_analytics_uri = log_analytics_uri
        self.workspace_id = workspace_id
        self.shared_key = shared_key

    def _build_signature(self, workspace_id, shared_key, date, content_length, method, content_type, resource):
        x_headers = 'x-ms-date:' + date
        string_to_hash = method + '\n' + str(content_length) + '\n' + content_type + '\n' + x_headers + '\n' + resource
        bytes_to_hash = bytes(string_to_hash, encoding='utf-8')
        decoded_key = base64.b64decode(shared_key)
        encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
        authorization = f'SharedKey {workspace_id}:{encoded_hash}'
        return authorization

    def _make_request(self, uri, body, headers):
        response = requests.post(uri, data=body, headers=headers)
        if not (200 <= response.status_code <= 299):
            raise Exception(f'Error during sending events to Azure Sentinel. Response: {response.status_code} - {response.text}')

    def _post_data(self, workspace_id, shared_key, body, log_type):
        events_number = len(body)
        body = json.dumps(body)
        method = 'POST'
        content_type = 'application/json'
        resource = '/api/logs'
        rfc1123date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        content_length = len(body)
        signature = self._build_signature(workspace_id, shared_key, rfc1123date, content_length, method, content_type, resource)
        uri = self.log_analytics_uri + resource + '?api-version=2016-04-01'

        headers = {
            'content-type': content_type,
            'Authorization': signature,
            'Log-Type': 'PerceptionPoint_' + log_type,
            'x-ms-date': rfc1123date
        }

        try_number = 1
        while True:
            try:
                self._make_request(uri, body, headers)
            except Exception as err:
                if try_number < 3:
                    logging.warning(f'Error while sending data to Azure Sentinel. Try number: {try_number}. {err}')
                    time.sleep(try_number)
                    try_number += 1
                else:
                    logging.error(str(err))
                    raise err
            else:
                logging.info(f'{events_number} events have been successfully sent to Azure Sentinel')
                break

    def send(self, scans, log_type):
        try:
            self._post_data(self.workspace_id, self.shared_key, scans, log_type)
        except Exception as err:
            logging.warning(f'{err}')


class APIBaseConnector:
    SCANS_ENDPOINT = '/api/v1/scans/list/'
    AUDITS_ENDPOINT = '/api/v1/audit-events/'

    def __init__(self, token, base_url, org_id, log_analytics_uri, workspace_id, shared_key):
        self.api = requests.Session()
        self.token = token
        self.api.headers = self.headers
        self.base_url = base_url
        self.end_time = None
        self.start_time = None
        self.org_id = org_id
        self._organization = None
        self.sentinel = MicrosoftSentinelConnector(log_analytics_uri, workspace_id, shared_key)

    @property
    def headers(self):
        return {'Authorization': f'Token {self.token}'}
    
    @property
    def base_params(self):
        params = {
            'organization_id': self.organization['id'],
            'start': int(self.start_time),
            'end': int(self.end_time),
        }
        return params
    
    @property
    def organization(self):
        if self._organization is None:
            r = self.get(f'/api/organizations/{self.org_id}/')
            self._organization = r.json()
            self.set_base_url(self._organization.get('environment', {}).get('api_url'))
        return self._organization

    def set_base_url(self, url):
        self.base_url = url

    def set_time_range(self):
        if self.start_time is None:
            end_time = datetime.now(timezone.utc).timestamp()
            start_time = end_time - 60*5
        else:
            end_time = datetime.now(timezone.utc).timestamp()
            start_time = self.end_time + 0.1

        self.start_time = start_time
        self.end_time = end_time


    def get(self, url, **kwargs):
        modified_url = urljoin(self.base_url, url)
        return self.api.get(modified_url, **kwargs)

    def fetch_data(self, url=None, params={}):
        if url:
            endpoint = url
        else:
            endpoint = self.SCANS_ENDPOINT
        response = self.get(endpoint, params=params)
        return response.status_code, response
    
    def fetch_scans_chunks(self):
        status_code, scans = self.fetch_data(
            url=self.SCANS_ENDPOINT,
            params={
                **self.base_params,
                # 'count_agg[]': 'verbose_verdict',
                '!whitelist_tags': 'simulation',
                '!sample_type_str': 'outbound-email',
                'limit': 500
            }
        )
        
        if status_code != 200:
            logging.warning(f'ERROR: {status_code}, {scans.text}')
            return {}

        scans = scans.json()
        yield scans
        if not scans['has_more']:
            return {}
        
        while scans['has_more']:
            status_code, scans = self.fetch_data(
                url=scans['next'],
                params={
                    **self.base_params,
                    # 'count_agg[]': 'verbose_verdict',
                    '!whitelist_tags': 'simulation',
                    '!sample_type_str': 'outbound-email',
                    'limit': 500
                }
            )

            if status_code != 200:
                logging.warning(f'ERROR: {status_code}, {scans.text}')
                return {}

            scans = scans.json()
            yield scans
    
    def fetch_audits_chunks(self):
        status_code, audits = self.fetch_data(
            url=self.AUDITS_ENDPOINT,
            params={
                **self.base_params,
                'limit': 500
            }
        )
        
        if status_code != 200:
            logging.warning(f'ERROR: {status_code}, {audits.text}')
            return {}

        audits = audits.json()
        yield audits
        if not audits['has_more']:
            return {}
        
        while audits['has_more']:
            status_code, audits = self.fetch_data(
                url=audits['next'],
                params={
                    **self.base_params,
                    'limit': 500
                }
            )

            if status_code != 200:
                logging.warning(f'ERROR: {status_code}, {audits.text}')
                return {}

            audits = audits.json()
            yield audits

    def post_to_sentinel(self, log_type):
        self.set_time_range()

        if log_type == 'Scans':
            for result in self.fetch_scans_chunks():
                if result is not {}:
                    self.sentinel.send(result['results'], log_type)

        elif log_type == 'Audits':
            for result in self.fetch_audits_chunks():
                if result is not {}:
                    self.sentinel.send(result['results'], log_type)
