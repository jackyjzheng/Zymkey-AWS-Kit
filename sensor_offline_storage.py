from Queue import Queue
from threading import Thread, Semaphore
import sys
import os
import time
import urllib2
import json
import boto3
import hashlib
import binascii
import pycurl
from OpenSSL import SSL
import random
import datetime
import zymkey

# Global variables for the script
LOGFILE_NAME = 'log.txt'
TOPIC = 'Zymkey'
DEVICE_ID = '1'
IP = '192.168.12.28'
DATA_SEP = '---NEW ITEM---'
FAIL_THRESHOLD = 100

failQ = Queue() # The queue of JSON objects that failed to upload
qSem = Semaphore() # The semaphore for putting and getting from the queue
rwSem = Semaphore() # The semaphore for reading and writing from the log

# Create a log.txt file if it doesn't already exist
cur_dir = os.path.dirname(os.path.realpath(__file__))
log_path = os.path.join(cur_dir, LOGFILE_NAME)
if not os.path.isfile(log_path):
  f = open(log_path,"w+")
  f.close()

# Variables to setup the AWS endpoint for publishing data
boto3client = boto3.client('iot')
AWS_ENDPOINT = "https://" + str(boto3client.describe_endpoint()['endpointAddress']) + ":8443/topics/" + TOPIC + "?qos=1"    

# Function using pycurl to upload data to the topic 
def ZK_AWS_Publish(url, post_field, CA_Path, Cert_Path,):
  #Setting Curl to use zymkey_ssl engine
  c = pycurl.Curl()
  c.setopt(c.SSLENGINE, "zymkey_ssl")
  c.setopt(c.SSLENGINE_DEFAULT, 1L)
  c.setopt(c.SSLVERSION, c.SSLVERSION_TLSv1_2)
  
  #Settings certificates for HTTPS connection
  c.setopt(c.SSLENGINE, "zymkey_ssl")
  c.setopt(c.SSLCERTTYPE, "PEM")
  c.setopt(c.SSLCERT, Cert_Path)
  c.setopt(c.CAINFO, CA_Path)
  
  #setting endpoint and HTTPS type, here it is a POST
  c.setopt(c.URL, url)
  c.setopt(c.POSTFIELDS, post_field)
  
  #Telling Curl to do client and host authentication
  c.setopt(c.SSL_VERIFYPEER, 1)
  c.setopt(c.SSL_VERIFYHOST, 2)
  
  #Turn on Verbose output and set key as placeholder, not actually a real file.
  c.setopt(c.VERBOSE, 0)
  c.setopt(c.SSLKEYTYPE, "ENG") 
  c.setopt(c.SSLKEY, "nonzymkey.key")
  c.setopt(c.TIMEOUT, 2)
  try:
    c.perform()
    return 0
  except Exception as e:
    return -1

# Checking if we can connect to one of Google's IP to check if our internet connection is up
def internet_on():
    try:
        urllib2.urlopen('http://216.58.192.142', timeout=2)
        return True
    except urllib2.URLError as err: 
        return False
    except Exception as e:
      print(e)

# This thread would check for any data failed to publish from the failQ queue and write it to a log file
def checkFailQueue():
  global internetOn
  while True:
    qSem.acquire()
    if failQ.qsize() > FAIL_THRESHOLD or (internetOn and (failQ.qsize() is not 0)):
      print('Queue has reached size ' + str(failQ.qsize()))
      rwSem.acquire()
      with open(log_path, "a") as myfile:
        numObjects = 0
        while failQ.qsize() > 0:
          data = failQ.get()
          myfile.write(DATA_SEP + '\n' + data + '\n') # Separate each data object by a characteristic line
          numObjects += 1
        print('Wrote ' + str(numObjects) + ' items from queue to log')
      rwSem.release() 
    qSem.release()

# This thread will check the log file for any failed events and retry sending them 
def retrySend():
  global internetOn
  while True:
    rwSem.acquire()
    if internetOn: # Connection is alive
      if not os.stat(log_path).st_size == 0: # There is data that needs to reupload
        numPublish = 1
        with open(log_path) as f:
          next(f) # Skip the first DATA_SEP tag
          dataBuilder = ''
          json_data = ''
          for line in f:
            line.rstrip() # Strip newline characters
            if DATA_SEP not in line:
              dataBuilder += line # Build up the JSON payload line by line of file
            else:
              json_data = dataBuilder # Reached the data separator string so now we store dataBuilder as the json data
              print('RETRY ITEM ' + str(numPublish) + ': ' + json_data)
              if ZK_AWS_Publish(url=AWS_ENDPOINT, post_field=json_data, CA_Path='/home/pi/Zymkey-AWS-Kit/bash_scripts/CA_files/zk_ca.pem', Cert_Path='/home/pi/Zymkey-AWS-Kit/zymkey.crt') is not -1:
                print('\tRETRY PUBLISH item ' + str(numPublish) + ' from retry\n')
              else:
                print('Couldnt publish ' + str(numPublish) + ', added to queue')
                failQ.put(json_data)
              numPublish += 1
              dataBuilder = '' # Reset the dataBuilder to empty string
          # Print out the very last item in the file
          json_data = dataBuilder
          print('RETRY ITEM ' + str(numPublish) + ': ' + json_data)
          if ZK_AWS_Publish(url=AWS_ENDPOINT, post_field=json_data, CA_Path='/home/pi/Zymkey-AWS-Kit/bash_scripts/CA_files/zk_ca.pem', Cert_Path='/home/pi/Zymkey-AWS-Kit/zymkey.crt') is not -1:
            print('\tRETRY PUBLISH item ' + str(numPublish) + ' from retry\n')
          else:
            print('Couldnt publish ' + str(numPublish) + ' added to queue')
            failQ.put(json_data)
        f = open(log_path, 'w+') # Create a new blank log.txt for new logging
        f.close()
    rwSem.release()
    time.sleep(3) # Retrying the publish because isn't too essential to do in quick time

failThread = Thread(target = checkFailQueue)
retryThread = Thread(target = retrySend)

failThread.daemon = True
retryThread.daemon = True

internetOn = internet_on()
failThread.start()
retryThread.start()


try:
  while True:
    # Generate the sample data to try to send
    timestamp = datetime.datetime.now()
    temp_data = {"tempF": random.randint(70,100), "tempC" : random.randint(35, 50)}
    encrypted_data = zymkey.client.lock(bytearray(json.dumps(temp_data)))
    signature = zymkey.client.sign(encrypted_data)
    data = {"ip": IP, "signature": binascii.hexlify(signature), "encryptedData": binascii.hexlify(encrypted_data), "tempData": temp_data}
    post_field = {"deviceId": DEVICE_ID, "timestamp": str(timestamp), "data": data}
    json_data = json.dumps(post_field)

    if not internet_on():
      internetOn = False
      qSem.acquire()
      print('No connection detected...putting the data into offline storage')
      failQ.put(json_data)
      qSem.release()
    else:
      internetOn = True
      print('REGULAR PUBLISH item: ' + json_data)
      if ZK_AWS_Publish(url=AWS_ENDPOINT, post_field=json_data, CA_Path='/home/pi/Zymkey-AWS-Kit/bash_scripts/CA_files/zk_ca.pem', Cert_Path='/home/pi/Zymkey-AWS-Kit/zymkey.crt') is -1:
        failQ.put(json_data)
      print('\tREGULAR PUBLISH: Fail queue size: ' + str(failQ.qsize()) + '\n')
except KeyboardInterrupt:
  print('Exiting...')
  sys.exit()
