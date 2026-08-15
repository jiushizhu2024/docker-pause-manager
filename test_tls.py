import docker
import os

# Set environment to disable TLS
os.environ['DOCKER_TLS_VERIFY'] = '0'
os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'

print("Trying with DOCKER_HOST set...")
try:
    cli = docker.from_env()
    print("docker.from_env() OK")
    print(cli.version())
except Exception as e:
    print("FAILED:", str(e)[:200])

print("\nTrying with explicit base_url...")
try:
    cli = docker.DockerClient(
        base_url="unix:///var/run/docker.sock",
        version="auto",
        timeout=10
    )
    print("DockerClient OK")
    print(cli.version())
except Exception as e:
    print("FAILED:", str(e)[:200])
