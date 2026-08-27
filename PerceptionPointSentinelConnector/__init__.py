import os
import logging
import azure.functions as func
from .connectors import APIBaseConnector

logger = logging.getLogger(__name__)

BASE_API = os.environ['PP_BASE_API']
PP_AUTH_TOKEN = os.environ['PP_AUTH_TOKEN']
ORG_ID = os.environ['PP_ORG_ID']
DCE_ENDPOINT = os.environ['DCE_ENDPOINT']
DCR_IMMUTABLE_ID = os.environ['DCR_IMMUTABLE_ID']

log_types = {'Scans', 'Audits'}

app = func.FunctionApp()

def main(mytimer: func.TimerRequest) -> None:
    if mytimer.past_due:
        logger.info('The timer is past due!')

    logger.info('Starting function')
    connector = APIBaseConnector(token=PP_AUTH_TOKEN,
                                base_url=BASE_API,
                                org_id=ORG_ID,
                                dce_endpoint=DCE_ENDPOINT,
                                dcr_immutable_id=DCR_IMMUTABLE_ID)

    for log_type in log_types:
        logger.info(f'Processing log_type={log_type}')
        connector.post_to_sentinel(log_type)

    logger.info('function executed.')
