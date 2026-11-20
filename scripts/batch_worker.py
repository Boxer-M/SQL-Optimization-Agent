"""Separate serial SQL worker. SIGTERM marks interruption; SIGKILL recovered by next worker."""
import argparse,signal,time
from sql_optimization_agent.batch_jobs import work_once


def stop(*args):raise SystemExit(0)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    signal.signal(signal.SIGTERM,stop)
    while True:
        job=work_once()
        if args.once:return
        if job is None:time.sleep(1)

if __name__=='__main__':main()
