import logging
import requests
from urllib.parse import urljoin
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.monitor.ingestion import LogsIngestionClient
from azure.core.exceptions import HttpResponseError

logger = logging.getLogger(__name__)


class MicrosoftSentinelConnector:
    def __init__(self, dce_endpoint, dcr_immutable_id):
        logger.info(f'Initializing Microsoft Sentinel connector for DCE endpoint {dce_endpoint}')
        self.dcr_immutable_id = dcr_immutable_id
        self.client = LogsIngestionClient(endpoint=dce_endpoint, credential=DefaultAzureCredential())

    @staticmethod
    def _to_logs(records):
        time_generated = datetime.now(timezone.utc).isoformat()
        return [{'TimeGenerated': time_generated, 'RawData': record} for record in records]

    def _on_upload_error(self, error):
        logger.warning(f'Error while sending a chunk of events to Azure Sentinel: {error.error}')

    def send(self, scans, log_type):
        if not scans:
            logger.debug(f'No {log_type} events to send to Azure Sentinel, skipping upload')
            return

        stream_name = f'Custom-PerceptionPoint{log_type}'
        logs = self._to_logs(scans)
        logger.info(f'Sending {len(logs)} {log_type} events to Azure Sentinel stream {stream_name}')

        try:
            self.client.upload(rule_id=self.dcr_immutable_id, stream_name=stream_name, logs=logs, on_error=self._on_upload_error)
        except HttpResponseError as err:
            logger.error(f'Error while sending data to Azure Sentinel. {err}')
        else:
            logger.info(f'{len(logs)} events have been successfully sent to Azure Sentinel')


class APIBaseConnector:
    SCANS_ENDPOINT = '/api/v1/scans/list/'
    AUDITS_ENDPOINT = '/api/v1/audit-events/'

    def __init__(self, token, base_url, org_id, dce_endpoint, dcr_immutable_id):
        logger.info(f'Initializing API connector for organization {org_id} against base URL {base_url}')
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
    def scan_params(self):
        params = {
            'organization_id': self.organization['id'],
            'start': int(self.start_time),
            'end': int(self.end_time),
        }
        return params

    @property
    def audit_params(self):
        # Audits send start only (no end), so the API returns up to now.
        params = {
            'organization_id': self.organization['id'],
            'start': int(self.start_time),
        }
        return params
    
    @property
    def organization(self):
        if self._organization is None:
            logger.info(f'Fetching organization details for org_id {self.org_id}')
            r = self.get(f'/api/organizations/{self.org_id}/')
            self._organization = r.json()
            api_url = self._organization.get('environment', {}).get('api_url')
            logger.info(f'Resolved organization {self.org_id} to API URL {api_url}')
            self.set_base_url(api_url)
        return self._organization

    def set_base_url(self, url):
        logger.debug(f'Setting base URL to {url}')
        self.base_url = url

    def set_time_range(self):
        end_time = datetime.now(timezone.utc).timestamp()
        start_time = end_time - 60*5
        self.start_time = start_time
        self.end_time = end_time
        logger.info(f'Time range set to {self.start_time} - {self.end_time}')

    def set_audit_start(self):
        self.start_time = datetime.now(timezone.utc).timestamp() - 60*5
        self.end_time = None


    def get(self, url, **kwargs):
        modified_url = urljoin(self.base_url, url)
        logger.debug(f'GET {modified_url} params={kwargs.get("params")}')
        return self.api.get(modified_url, **kwargs)

    def fetch_data(self, url=None, params={}):
        if url:
            endpoint = url
        else:
            endpoint = self.SCANS_ENDPOINT
        response = self.get(endpoint, params=params)
        logger.debug(f'Received response {response.status_code} from {endpoint}')
        return response.status_code, response

    def fetch_scans_chunks(self):
        logger.info('Starting to fetch scans from Perception Point API')
        status_code, scans = self.fetch_data(
            url=self.SCANS_ENDPOINT,
            params={
                **self.scan_params,
                # 'count_agg[]': 'verbose_verdict',
                '!whitelist_tags': 'simulation',
                '!sample_type_str': 'outbound-email',
                'limit': 500
            }
        )

        if status_code != 200:
            logger.warning(f'ERROR: {status_code}, {scans.text}')
            return {}

        scans = scans.json()
        logger.info(f'Fetched chunk of {len(scans.get("results", []))} scans')
        yield scans
        if not scans['has_more']:
            logger.info('No more scans to fetch')
            return {}

        while scans['has_more']:
            status_code, scans = self.fetch_data(
                url=scans['next'],
                params={
                    **self.scan_params,
                    # 'count_agg[]': 'verbose_verdict',
                    '!whitelist_tags': 'simulation',
                    '!sample_type_str': 'outbound-email',
                    'limit': 500
                }
            )

            if status_code != 200:
                logger.warning(f'ERROR: {status_code}, {scans.text}')
                return {}

            scans = scans.json()
            logger.info(f'Fetched chunk of {len(scans.get("results", []))} scans')
            yield scans

    def fetch_audits_chunks(self):
        logger.info('Starting to fetch audit events from Perception Point API')
        status_code, audits = self.fetch_data(
            url=self.AUDITS_ENDPOINT,
            params={
                **self.audit_params,
                'limit': 500
            }
        )

        if status_code != 200:
            logger.warning(f'ERROR: {status_code}, {audits.text}')
            return {}

        audits = audits.json()
        logger.info(f'Fetched chunk of {len(audits.get("results", []))} audit events')
        yield audits
        if not audits['has_more']:
            logger.info('No more audit events to fetch')
            return {}

        while audits['has_more']:
            status_code, audits = self.fetch_data(
                url=audits['next'],
                params={
                    **self.audit_params,
                    'limit': 500
                }
            )

            if status_code != 200:
                logger.warning(f'ERROR: {status_code}, {audits.text}')
                return {}

            audits = audits.json()
            logger.info(f'Fetched chunk of {len(audits.get("results", []))} audit events')
            yield audits

    def post_to_sentinel(self, log_type):
        if log_type == 'Audits':
            self.set_audit_start()
        else:
            self.set_time_range()

        if log_type == 'Scans':
            for result in self.fetch_scans_chunks():
                if result is not {}:
                    self.sentinel.send(result['results'], log_type)

        elif log_type == 'Audits':
            for result in self.fetch_audits_chunks():
                if result is not {}:
                    self.sentinel.send(result['results'], log_type)

        logger.info(f'Finished post_to_sentinel for log_type={log_type}')
