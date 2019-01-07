import json
import os
import boto3
import zipfile
import tempfile
import traceback
import botocore
from botocore.vendored import requests
from boto3.session import Session
code_pipeline = boto3.client('codepipeline')
def get_deployment_id():
    '''
    Query the codepipeline state and get the name of the latest deploymentId
    '''
    client = boto3.client('codepipeline')
    response = client.get_pipeline_state(name='test-pipeline')
    return response['stageStates'][1]['actionStates'][0]['latestExecution']['externalExecutionId']

def get_deployment_status(id='None'):
    client = boto3.client('codedeploy')
    response = client.batch_get_deployments(deploymentIds=[id])
    return response['deploymentsInfo'][0]['status']


def setup_s3_client(job_data):
    """Creates an S3 client

    Uses the credentials passed in the event by CodePipeline. These
    credentials can be used to access the artifact bucket.

    Args:
        job_data: The job data structure

    Returns:
        An S3 client with the appropriate credentials

    """
    key_id = job_data['artifactCredentials']['accessKeyId']
    key_secret = job_data['artifactCredentials']['secretAccessKey']
    session_token = job_data['artifactCredentials']['sessionToken']    
    session = Session(aws_access_key_id=key_id,
                      aws_secret_access_key=key_secret,
                      aws_session_token=session_token)
    return session.client('s3', config=botocore.client.Config(signature_version='s3v4'))
    
def get_data_from_s3(s3, artifact):
    tmp_file = tempfile.NamedTemporaryFile()
    bucket = artifact['location']['s3Location']['bucketName']
    key = artifact['location']['s3Location']['objectKey']
    file_in_zip = 'CODE_BUILD_DATA' # Will be passed as an Environment Variable to get the GitBranch and CommitID details
    with tempfile.NamedTemporaryFile() as tmp_file:
        s3.download_file(bucket, key, tmp_file.name)
        with zipfile.ZipFile(tmp_file.name, 'r') as zip:
            return zip.read(file_in_zip)

def post_status_on_pr(data, deployment_status):
    
    headers = {
        'Authorization': 'token '+os.environ['GIT_ACCESS_TOKEN'],
        'Content-Type': 'application/json'
    }
    input_data = data.split(" ")
    CODEBUILD_SOURCE_REPO_URL = input_data[0]
    owner, repo = CODEBUILD_SOURCE_REPO_URL.split("/")[-2:]
    repo = repo.split(".")[0]
    CODEBUILD_SOURCE_VERSION = input_data[1].split("\n")[0]
    commit_id = ''
    if '/' in CODEBUILD_SOURCE_VERSION:
        pr_num = CODEBUILD_SOURCE_VERSION.split("/")[1]
        GET_COMMENTS_ON_PR = "https://api.github.com/repos/"+owner+"/"+repo+"/pulls/"+pr_num+"/commits"
        print GET_COMMENTS_ON_PR
        r = requests.get(url=GET_COMMENTS_ON_PR,headers=headers, verify=True).json()
        commit_id = r[-1]['sha'] # latest commit on a PR 
    else:
        commit_id = CODEBUILD_SOURCE_VERSION.split("\n")[0]
    
    print('CODEBUILD_SOURCE_VERSION', CODEBUILD_SOURCE_VERSION, commit_id)
    GITHUB_URL = "https://api.github.com/repos/"+owner+"/"+repo+"/statuses/"+commit_id
    state = 'failure'
    description = 'AWS Code Deploy failed!!!!!'
    context = 'AWS Code Deploy failed'
    print("Deployment status ", deployment_status)
    if deployment_status == 'Succeeded':
        state = "success"
        description = "AWS Code Deploy success!!!!!"
        context = "AWS Code Deploy success"
    data ={
      "state": state,
      "target_url": "https://example.com/build/status",
      "description": description,
      "context": context
    }


   
    r = requests.post(url=GITHUB_URL, data=json.dumps(data), headers=headers).json()
    print r
    

def put_job_failure(job, message):
    """Notify CodePipeline of a failed job

    Args:
        job: The CodePipeline job ID
        message: A message to be logged relating to the job status

    Raises:
        Exception: Any exception thrown by .put_job_failure_result()

    """
    print('Putting job failure')
    print(message)
    code_pipeline.put_job_failure_result(jobId=job, failureDetails={'message': message, 'type': 'JobFailed'})
    
def put_job_success(job, message):
    """Notify CodePipeline of a successful job

    Args:
        job: The CodePipeline job ID
        message: A message to be logged relating to the job status

    Raises:
        Exception: Any exception thrown by .put_job_success_result()

    """
    print('Putting job success')
    print(message)
    code_pipeline.put_job_success_result(jobId=job)

def lambda_handler(event, context):
    deployment_id = get_deployment_id()
    deployment_status = get_deployment_status(id=str(deployment_id))
    print("Event Data", event)
    try:
        # Extract the Job ID
        job_id = event['CodePipeline.job']['id']

        # Extract the Job Data
        job_data = event['CodePipeline.job']['data']

        artifacts = job_data['inputArtifacts']
        print("Artifacts ", artifacts)
        # Get S3 client to access artifact with
        s3 = setup_s3_client(job_data)
        # Get the JSON template file out of the artifact
        s3_data = get_data_from_s3(s3, artifacts[0])
        #Process details from the file
        post_status_on_pr(s3_data, deployment_status)
        
        put_job_success(job_id, 'Posted on the commit ')
    except Exception as e:
        # If any other exceptions which we didn't expect are raised
        # then fail the job and log the exception message.
        print('Function failed due to exception.')
        print(e)
        traceback.print_exc()
        put_job_failure(job_id, 'Function exception: ' + str(e))

    print('Function complete.')
    return "Complete."
    #     return {
    #     'statusCode': 200,
    #     'body': json.dumps('Hello from Lambda!')
    # }
    

