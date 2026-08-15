import subprocess
import json

# Test using docker CLI directly
result = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], 
                       capture_output=True, text=True)
print("Docker server version:", result.stdout.strip())
print("Return code:", result.returncode)

# List containers
result = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True)
containers = result.stdout.strip().split('\n') if result.stdout.strip() else []
print(f"Running containers: {len(containers)}")
for c in containers[:5]:
    print(f"  - {c}")
