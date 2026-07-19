import logging
import requests
from urllib.parse import urljoin
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.monitor.ingestion import LogsIngestionClient
from azure.core.exceptions import HttpResponseError


class MicrosoftSentinelConnector:
    def __init__(self, dce_endpoint, dcr_immutable_id):
        self.dcr_immutable_id = dcr_immutable_id
        self.client = LogsIngestionClient(endpoint=dce_endpoint, credential=DefaultAzureCredential())

    @staticmethod
    def _to_logs(records):
        time_generated = datetime.now(timezone.utc).isoformat()
        return [{'TimeGenerated': time_generated, 'RawData': record} for record in records]

    def _on_upload_error(self, error):
        logging.warning(f'Error while sending a chunk of events to Azure Sentinel: {error.error}')

    def send(self, scans, log_type):
        if not scans:
            return

        stream_name = f'Custom-PerceptionPoint{log_type}'
        logs = self._to_logs(scans)

        try:
            self.client.upload(rule_id=self.dcr_immutable_id, stream_name=stream_name, logs=logs, on_error=self._on_upload_error)
        except HttpResponseError as err:
            logging.warning(f'Error while sending data to Azure Sentinel. {err}')
        else:
            logging.info(f'{len(logs)} events have been successfully sent to Azure Sentinel')


class APIBaseConnector:
    SCANS_ENDPOINT = '/api/v1/scans/list/'
    AUDITS_ENDPOINT = '/api/v1/audit-events/'

    def __init__(self, token, base_url, org_id, dce_endpoint, dcr_immutable_id):
        self.api = requests.Session()
        self.token = token
        self.api.headers = self.headers
        self.base_url = base_url
        self.end_time = None
        self.start_time = None
        self.org_id = org_id
        self._organization = None
        self.sentinel = MicrosoftSentinelConnector(dce_endpoint, dcr_immutable_id)

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
