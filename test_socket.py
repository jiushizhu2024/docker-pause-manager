import docker
cli = docker.DockerClient(base_url='unix:///var/run/docker.sock')
print(cli.version())
