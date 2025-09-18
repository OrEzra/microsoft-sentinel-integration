import os
import logging
import azure.functions as func
from .connectors import APIBaseConnector

BASE_API = os.environ['PP_BASE_API']
PP_AUTH_TOKEN = os.environ['PP_AUTH_TOKEN']
ORG_ID = os.environ['PP_ORG_ID']
WORKSPACE_ID = os.environ['WORKSPACE_ID']
SHARED_KEY = os.environ['SHARED_KEY']
LOG_ANALYTICS_URI = os.environ['LOG_ANALYTICS_URI']

log_types = {'Scans', 'Audits'}

app = func.FunctionApp()

def main(mytimer: func.TimerRequest) -> None:
    if mytimer.past_due:
        logging.info('The timer is past due!')

    logging.info('Starting function')
    connector = APIBaseConnector(token=PP_AUTH_TOKEN, 
                                base_url=BASE_API, 
                                org_id=ORG_ID, 
                                log_analytics_uri=LOG_ANALYTICS_URI, 
                                workspace_id=WORKSPACE_ID, 
                                shared_key=SHARED_KEY)
    
    for log_type in log_types:
        connector.post_to_sentinel(log_type)
    logging.info('function executed.')
