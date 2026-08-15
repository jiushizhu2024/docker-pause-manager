import docker
import json

# Test Unix socket connection
cli = docker.DockerClient(base_url='unix:///var/run/docker.sock')
version = cli.version()
print(f"Docker version: {version.get('Version', 'unknown')}")
print(f"API version: {version.get('ApiVersion', 'unknown')}")

# List containers
containers = cli.containers.list()
print(f"Running containers: {len(containers)}")
