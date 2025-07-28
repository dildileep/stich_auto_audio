import os
import json
import logging
import boto3
from gtts import gTTS
from pydub import AudioSegment
import hashlib
import tempfile
import re
from collections import namedtuple
from io import BytesIO

# Set up logging
log = logging.getLogger()
log.setLevel(logging.INFO)

LambdaOptions = namedtuple('LambdaOptions', ['message', 'audios', 'output'])
FileInfo = namedtuple('FileInfo', ['bucket', 'name', 'key', 'hash'])
StitchFile = namedtuple('StitchFile', ['start', 'end', 'info'])

CLEAN_CHARACTERS = "abcdefghijklmnopqrstuvwxyz1234567890 "
ALLOWED_EXTENSIONS = ['.wav']


def get_hash(binary):
    return hashlib.md5(binary).hexdigest()


class S3Repo:
    def __init__(self, audios, output):
        self.s3 = boto3.client('s3')
        self.bucket, self.prefix = self._split_s3(audios)
        self.out_bucket, self.out_key = self._split_s3(output)
        self.files_cache = list(self.load_files())

    def _split_s3(self, s3_path):
        parts = s3_path.split('/', 1)
        return parts[0], parts[1] if len(parts) > 1 else ''

    def list_keys(self, bucket, prefix):
        paginator = self.s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                yield obj['Key']

    def load_files(self):
        for key in self.list_keys(self.bucket, self.prefix):
            if key.endswith('.wav'):
                name = os.path.splitext(os.path.basename(key))[0]
                audio = self.s3.get_object(Bucket=self.bucket, Key=key)['Body'].read()
                yield FileInfo(self.bucket, name, key, get_hash(audio))

    def read(self, key):
        # Return BytesIO instead of raw StreamingBody
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return BytesIO(response['Body'].read())

    def write(self, audio):
        buffer = BytesIO()
        audio.export(buffer, format='wav')
        buffer.seek(0)
        self.s3.upload_fileobj(buffer, Bucket=self.out_bucket, Key=self.out_key)
        log.info(f"Uploaded stitched audio to s3://{self.out_bucket}/{self.out_key}")

    def generate_missing(self, word):
        mp3_path = f'/tmp/{word}.mp3'
        wav_path = f'/tmp/{word}.wav'
        s3_key = f'{self.prefix}/{word}.wav'

        tts = gTTS(text=word, lang='en')
        tts.save(mp3_path)

        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(wav_path, format='wav')
        os.remove(mp3_path)

        with open(wav_path, 'rb') as f:
            self.s3.upload_fileobj(f, Bucket=self.bucket, Key=s3_key)
            f.seek(0)
            hashval = get_hash(f.read())

        os.remove(wav_path)
        fileinfo = FileInfo(self.bucket, word, s3_key, hashval)
        self.files_cache.append(fileinfo)
        return fileinfo

    def files(self):
        return self.files_cache


def clean(text):
    return re.sub(r'\s+', ' ', ''.join(c for c in text.lower() if c in CLEAN_CHARACTERS))


def main(message, repo):
    clean_msg = clean(message)
    log.info(f"Cleaned message: {clean_msg}")

    segments = []
    for word in clean_msg.split():
        match = next((f for f in repo.files() if f.name == word), None)
        if match:
            segments.append(StitchFile(clean_msg.find(word), clean_msg.find(word) + len(word), match))
        else:
            log.info(f"Generating missing word: {word}")
            match = repo.generate_missing(word)
            segments.append(StitchFile(clean_msg.find(word), clean_msg.find(word) + len(word), match))

    if not segments:
        log.warning("No audio segments found.")
        return False

    # Stitch audio segments
    segments.sort(key=lambda s: s.start)
    audio = None
    for s in segments:
        seg_audio = AudioSegment.from_file(repo.read(s.info.key), format='wav')
        audio = seg_audio if audio is None else audio + seg_audio

    repo.write(audio)
    return True


def lambda_handler(event, context):
    log.info(f"Received event: {json.dumps(event)}")

    try:
        body = json.loads(event['body'])
        message = body.get("message")
        audios = body.get("audios")
        output = body.get("output")
    except Exception as e:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON format or missing keys", "details": str(e)})
        }

    if not message or not audios or not output:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing one or more required fields: 'message', 'audios', 'output'"})
        }

    try:
        opts = LambdaOptions(message, audios, output)
        repo = S3Repo(opts.audios, opts.output)
        success = main(opts.message, repo)

        return {
            "statusCode": 200,
            "body": json.dumps({"success": success})
        }

    except Exception as e:
        log.exception("Unhandled exception:")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
