"""Read-only, real-hardware throughput test for 1–10 emulator connections.

Usage (inside activated OmniFlow venv):
    python tools/benchmark_mumu.py --instances 10 --seconds 20

Sends no taps. Measures ADB frame acquisition + normalization; does not imply
workflow/OCR throughput or guarantee 30 FPS from the independent video preview.
"""
import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
from omniflow.devices import Device, discover_mumu
from swordx.detection import normalize


def measure(serial, seconds):
    device=Device(serial=serial)
    samples=[]
    failed=0
    until=time.monotonic()+seconds
    try:device.ensure_connected()
    except Exception as exc:return serial,[],1,str(exc)
    while time.monotonic()<until:
        started=time.monotonic()
        try:
            frame=normalize(device.screenshot())
            if frame.shape!=(960,540,3):raise ValueError(f'Incorrect frame {frame.shape}')
            samples.append(time.monotonic()-started)
        except Exception as exc:
            failed+=1
            if failed>=5:return serial,samples,failed,str(exc)
    return serial,samples,failed,''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--instances',type=int,default=10)
    parser.add_argument('--seconds',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.instances<=10 or not 3<=args.seconds<=300:
        parser.error('instances 1–10; seconds 3–300')
    cv2.setNumThreads(1)
    devices=discover_mumu()[:args.instances]
    if not devices:
        print('No MuMu devices detected. Start your emulators first.');return 1
    print(f'Testing {len(devices)} emulator(s), read-only, {args.seconds}s each.')
    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        futures=[pool.submit(measure,serial,args.seconds) for serial in devices]
        for future in futures:
            serial, times, errors, message=future.result()
            if times:
                sorted_times=sorted(times)
                p95=sorted_times[min(len(times)-1,int(len(times)*.95))]
                print(f'{serial:<25} frames={len(times):>4}  avg={statistics.mean(times)*1000:>6.0f}ms  '
                      f'p95={p95*1000:>6.0f}ms  errors={errors}')
            else:print(f'{serial:<25} no frames: {message}')
    print('Use poll intervals >= measured capture latency; OCR and game workloads add overhead.')
    return 0


if __name__=='__main__':raise SystemExit(main())
