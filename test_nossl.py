import docker
import os

# Explicitly disable TLS
os.environ['DOCKER_TLS_VERIFY'] = '0'

print("Trying with tls=False...")
try:
    cli = docker.DockerClient(
        base_url="unix:///var/run/docker.sock",
        version="auto",
        timeout=10,
        tls=False
    )
    print("DockerClient with tls=False OK")
    print(cli.version())
except Exception as e:
    print("FAILED:", str(e)[:200])
