import docker
import os

print("Docker SDK path:", docker.__file__)
print("Docker socket exists:", os.path.exists("/var/run/docker.sock"))

try:
    cli = docker.from_env()
    print("docker.from_env() OK")
except Exception as e:
    print("docker.from_env() FAILED:", str(e)[:200])

try:
    cli = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    print("DockerClient with unix socket OK")
    print(cli.version())
except Exception as e:
    print("DockerClient FAILED:", str(e)[:200])
