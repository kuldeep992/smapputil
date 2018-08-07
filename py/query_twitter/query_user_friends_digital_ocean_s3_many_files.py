'''
Queries twitter for user metadata of friends of the input users.

The output is one json file of user metadata per input user.
So for 60 inputs, we get 60 json files.

There's no need to attach a volume for this query.
Tokens are pooled using tkpool.

Leon Yin 2017-11-06
updated 2018-08-06
'''

import argparse
import datetime
import logging
import json
import csv
import sys
import os
import time
import socket
from subprocess import Popen, PIPE

import s3
import digitalocean
from tweepy import Cursor, TweepError


from tkpool.tkpool.tweepypool import TweepyPool
from utils import prep_s3, settle_affairs_in_s3, destroy_droplet, get_id_list, get_ip_address, log


def parse_args(args):
    '''
    Which arguments we'll need
    '''
    parser = argparse.ArgumentParser()

    parser.add_argument('-i', '--input', dest='input', required=True, help='This is a path to your input.json, a [] list of twitter ids.')
    parser.add_argument('-a', '--auth', dest='auth', required=True, help='This is the path to your oauth.json file for twitter')
    parser.add_argument('--filebase', dest='filebase', nargs='?', default='twitter_query', help='the_base_of_the_file')
    parser.add_argument('-d', '--digital-ocean-token', dest='token', required=False, help='DO access token', const=1, nargs='?', default=False)
    parser.add_argument('-b','--s3-bucket', dest='s3_bucket', required=True, help='s3 bucket, ie s3://leonyin would be leonyin')
    parser.add_argument('-r', '--s3-key', dest='s3_key', required=True, help='the path in the bucket.')
    parser.add_argument('--start-idx-api', dest='start_idx_api', type=int, default=0, help='the first token to use')
    parser.add_argument('--start-idx-input', dest='start_idx_input', type=int, default=0, help='the first input to query')
    
    return vars(parser.parse_args())


def build_context(args):
    '''
    This creates a dictionary of variables we'll be using throughout the script.
    args are from parse_args
    '''
    context = args
    currentdate = datetime.datetime.now().strftime("%Y-%m-%d")
    currentyear = datetime.datetime.now().strftime("%Y")
    currentmonth = datetime.datetime.now().strftime("%m")
    context['currentyear'], context['currentmonth'] = currentyear, currentmonth

    # digital ocean
    if not context['token']:
        context['token'] = os.environ.get('DO_TOKEN')
    manager = digitalocean.Manager(token=context['token'])
    my_droplets = manager.get_all_droplets()
    vols =  manager.get_all_volumes()
    mydrop = [_ for _ in my_droplets if _.ip_address == get_ip_address()][0]
    context['droplet'] = mydrop
    context['droplet_id'] = mydrop.id
    context['droplet_region'] = mydrop.region['slug']
    context['volume_directory'] = pylogs

    output_base = context['file_root'] + currentdate + '_' + \
        context['input'].split('/')[-1].replace('.csv', '.json')

    # AWS s3
    context['s3_path'] = os.path.join(
        's3://', context['s3_bucket'], context['s3_key'], 
        'output/user_friends_many/',
    )

    context['s3_log'] = os.path.join(
        's3://' + context['s3_bucket'], 'logs', output_base + '.log'
    )
    context['s3_log_done'] = os.path.join(
        's3://' + context['s3_bucket'], context['s3_key'],
        'logs/user_friends_many/', currentyear, currentmonth, 
        output_base + '.log'
    )
    context['s3_auth'] = os.path.join(
        's3://' + context['s3_bucket'], 'tokens/used', 
        os.path.basename(context['auth'])
    )
    
     # local stuff
    context['user'] = os.environ.get('USER')
   
    context['output'] = os.path.join(
        context['volume_directory'], output_base
    )
    context['log'] = os.path.join(
        context['volume_directory'], output_base.replace('.json', '.log')
    )
    
    return context


def twitter_query(context):
    '''
    Gets user ids, and feeds them into a function to query twitter.
    '''
    input_file = context['input']
    auth_file = context['auth']
    id_list = get_id_list(input_file)
    offset = context['start_idx_input']
    start_idx = context['start_idx_api']
    
    log('Creating oauth pool...')
    api_pool = TweepyPool(auth_file)
    for i, user_id in enumerate(id_list[ offset : ]):
        filename, s3_filename = get_user_id_file(user_id, context)
        if not s3.file_exists(s3_filename):
            log('writing user id: {} here'.format(user_id, filename)) 

            with open(filename, 'w+') as write_fd:
                for item in Cursor(api_pool.friends, id=user_id, count=5000).items():
                    tweet_item = json.loads(json.dumps(item._json))
                    tweet_item['smapp_original_user_id'] = user_id
                    tweet_item['smapp_timestamp'] = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S +0000')
                    write_fd.write(json.dumps(tweet_item)+ '\n')

            log('Sending file to s3: {}'.format(s3_filename))
            s3.disk_2_s3(filename, s3_filename)
            s3.disk_2_s3(context['log'], context['s3_log'])
            os.remove(filename)
        else: 
            log('{} already queried!!!'.format(user_id))
        log('>>> {} out of {}'.format(i + offset, len(id_list)))
        time.sleep(1)


if __name__ == '__main__':
    '''
    Parse the input flags,
    create a context dict of all variables we're going to use,
    Check to make sure the machine has a volume attached.
    start a log,
    query twitter,
    compress the returned json object from twitter,
    upload the compressed json and the log to s3
    destroy all files on the volume, detach, destroy.
    '''
    args = parse_args(sys.argv[1:])
    context = build_context(args)
    logging.basicConfig(filename=context['log'], level=logging.INFO)
    prep_s3(context)
    twitter_query(context)
    settle_affairs_in_s3(context)
    destroy_droplet(context)
