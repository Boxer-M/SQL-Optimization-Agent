"""Exercise a prebuilt image in an isolated Compose project; never call Qwen."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', default='compose-smoke.json')
    args = parser.parse_args()
    project = 'sql-image-smoke-' + secrets.token_hex(5)
    ports = [free_port() for _ in range(3)]
    token = secrets.token_hex(24)
    env = dict(os.environ)
    env.update(COMPOSE_PROJECT_NAME=project, APP_IMAGE=args.image,
               POSTGRES_PORT=str(ports[0]), REVIEW_DB_PORT=str(ports[1]),
               WORKBENCH_PORT=str(ports[2]),
               REVIEW_TOKENS=json.dumps({token: {'name': 'ci-reviewer', 'role': 'reviewer'}}))
    for key in ('POSTGRES_PASSWORD', 'POSTGRES_READER_PASSWORD', 'REVIEW_ADMIN_PASSWORD', 'REVIEW_DB_PASSWORD'):
        env[key] = secrets.token_hex(24)
    # Ignore a developer's .env. Credentials exist only in subprocess environment.
    compose = ['docker', 'compose', '--env-file', '/dev/null', '-p', project]
    for file in ('compose.yaml', 'compose.review.yaml', 'compose.workbench.yaml'):
        compose += ['-f', file]

    def dc(*parts):
        return subprocess.check_output(compose + list(parts), env=env, text=True)

    def request(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(f'http://127.0.0.1:{ports[2]}{path}', body,
                                     {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read()

    try:
        dc('config', '--quiet')
        dc('--profile', 'setup', 'run', '--pull', 'never', '--rm', 'seed-optimization')
        dc('up', '-d', '--no-build', '--wait', 'api', 'worker')
        assert b'<html' in request('/workbench').lower()
        migration = json.loads(subprocess.check_output(['docker', 'inspect', dc('ps', '-aq', 'migrate').strip()], text=True))[0]
        assert migration['State']['ExitCode'] == 0
        job = json.loads(request('/reviews/jobs', {'files': [{'name': 'orders.sql', 'content': Path('sql/examples/orders_date_2025_06_15.sql').read_text()}]}))
        deadline = time.monotonic() + 180
        while True:
            state = json.loads(request('/reviews/jobs/' + job['job_id']))
            if state['status'] in ('completed', 'failed', 'interrupted'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Worker job did not finish')
            time.sleep(1)
        assert state['status'] == 'completed', state['status']
        archive = json.loads(request('/reviews/batches/' + state['batch_id']))
        report = archive['report']
        assert report['summary'] == {'total': 1, 'completed': 1, 'rejected': 0, 'failed': 0}
        assert report['results'][0]['detail']['model_requests'] == 0
        images = {}
        for service in ('migrate', 'api', 'worker'):
            container = json.loads(subprocess.check_output(['docker', 'inspect', dc('ps', '-aq', service).strip()], text=True))[0]
            images[service] = container['Image']
        assert len(set(images.values())) == 1
        image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
        evidence = {'status': 'passed', 'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                    'image': args.image, 'platform': image['Os'] + '/' + image['Architecture'],
                    'service_image_ids': images, 'migration_exit_code': 0, 'api_accessible': True,
                    'job_status': state['status'], 'archive_summary': report['summary'],
                    'report': report, 'real_model_requests': 0,
                    'scope': 'Local prebuilt-image Compose smoke; GHCR publication not exercised'}
        Path(args.report).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('Compose smoke passed; sanitized evidence:', args.report)
    finally:
        # Only this script's unique project is removed. No existing volumes are touched.
        dc('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
