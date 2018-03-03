import OpenSSL
import boto3
import os
import json
import subprocess
import sys
from implementation.functions import *
from implementation.aws_setup import AWS_Setup

'''pip install boto3'''
'''pip install OpenSSL'''
'''libcurl4-openssl-dev, libssl-dev, pycurl'''
aws_credentials_path = os.path.expanduser("~") + '/.aws/credentials'
if not os.path.exists(aws_credentials_path):
  aws_key_id = raw_input("Please enter your AWS Key id: ")
  aws_secret_key = raw_input("Please enter your AWS secret key: ")
  aws_region = raw_input("Please enter your AWS Region: ")
  with open(aws_credentials_path, 'w') as f:
    f.write("[default]\n")
    f.write("aws_access_key_id = " + aws_key_id + "\n")
    f.write("aws_secret_access_key = " + aws_secret_key + "\n")
    f.write("region=" + aws_region)

#Need better argument validation
ca_cert_path = raw_input("Please enter the absolute file path for your CA cert. PEM file: ")
ca_key_path = raw_input("Please enter the absolute file path for your CA private key file: ")

Zymkey_Manager = Zymkey_Cert_Manager(ca_cert_path, ca_key_path)
#Creating csr. with Zymkey private key
Zymkey_Manager.gen_zymkey_csr(fileName="zymkey.csr", filePath="./")
#Signing Zymkey csr. to create device certificate
Zymkey_Manager.sign_csr_with_ca(filePath="./", csr_name="zymkey.csr", crt_name="zymkey.crt")

AWS_Manager = AWS_Cert_Manager(ca_cert_path=ca_cert_path, ca_key_path=ca_key_path, device_cert_path='zymkey.crt')
#Registering CA on AWS IoT
Verification_Pem = AWS_Manager.gen_verify_csr()
with open("verify.csr", 'w') as f:
  f.write(Verification_Pem)
Zymkey_Manager.sign_csr_with_ca(filePath="./", csr_name="verify.csr", crt_name="verify.crt")
if AWS_Manager.register_CA_AWS(verify_crt_path="verify.crt") == -1:
  sys.exit()

awsSetup = AWS_Setup()
awsSetup.sigSetup()
awsSetup.dbSetup()

#Registering Zymkey device certificate with AWS IoT
device_register_response = AWS_Manager.register_device_cert_AWS()
publish_cert_id = boto3.client('iot-data')
publish_cert_id.publish(
  topic='certID',
  qos=1,
  payload=json.dumps(device_register_response)
)

#Attach policy to this certificate allowing it to publish data
AWS_Manager.create_initial_policy(targetARN=device_register_response['certificateArn'])

