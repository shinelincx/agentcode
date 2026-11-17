"""Project test and staging commands. Business Git is read on the host."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
import xml.etree.ElementTree as ET

import yaml

WSL = ["wsl.exe", "-d", os.environ.get("AGENTCODE_WSL_DISTRO", "Debian"), "--"] if os.name == "nt" else []
OPENER = build_opener(ProxyHandler({}))
STAGING = {"river": (("api", "web"), 18082, "/api/stats"),
           "proj-b": (("backend", "frontend"), 18083, "/api/actuator/health/readiness")}


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def output(command):
    return run(command, stdout=subprocess.PIPE, encoding="utf-8").stdout.strip()


def docker(*args):
    return [*WSL, "docker", *args]


def image(name):
    return os.environ.get("AGENTCODE_REGISTRY_PREFIX", "") + name


def linux_path(path):
    drive, rest = os.path.splitdrive(str(path.resolve()))
    if not WSL:
        return rest
    if not re.fullmatch(r"[a-zA-Z]:", drive):
        raise ValueError("WSL staging requires a local drive")
    return "/mnt/" + drive[0].lower() + rest.replace("\\", "/")


@contextmanager
def running_wsl():
    # Keep Docker available while the Windows child (Maven or HTTP smoke) is active.
    process = subprocess.Popen([*WSL, "head", "-c", "1"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) if WSL else None
    try:
        yield
    finally:
        if process:
            process.communicate(b"\n", timeout=10)


def wait_until(probe, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = probe()
        except (OSError, subprocess.CalledProcessError, ValueError):
            value = None
        if value:
            return value
        time.sleep(1)
    raise RuntimeError("Service did not become ready before the deadline")


def maven(*arguments, cwd=None, env=None):
    tool = os.environ.get("MAVEN_CMD", "mvn.cmd" if os.name == "nt" else "mvn")
    run([tool, "-B", "-ntp", "-f", "backend/pom.xml", "-pl", "pcba-erp-admin", "-am", *arguments],
        cwd=cwd, env=env)


def proj_b_integration():
    suffix = uuid4().hex[:10]
    pg, redis = "agentcode-it-pg-" + suffix, "agentcode-it-redis-" + suffix
    started = []
    with running_wsl():
        try:
            # These published loopback ports and fixture credentials are fixed by upstream's integration tests.
            output(docker("run", "-d", "--rm", "--name", pg, "-p", "127.0.0.1:55432:5432",
                          "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_DB=pcba_erp",
                          "-e", "POSTGRES_PASSWORD=postgres-test-only", image("postgres:16-alpine")))
            started.append(pg)
            output(docker("run", "-d", "--rm", "--name", redis, "-p", "127.0.0.1:56379:6379",
                          image("redis:7.2.4-alpine"), "redis-server", "--requirepass", "redis-test-only"))
            started.append(redis)
            wait_until(lambda: subprocess.run(docker("exec", pg, "pg_isready", "-h", "127.0.0.1",
                                                   "-U", "postgres", "-d", "pcba_erp"),
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
            wait_until(lambda: output(docker("exec", "-e", "REDISCLI_AUTH=redis-test-only",
                                             redis, "redis-cli", "ping")) == "PONG")
            maven("verify", env={**os.environ, "PCBA_INTEGRATION_TESTS": "true"})
        finally:
            for name in reversed(started):
                run(docker("stop", name), stdout=subprocess.DEVNULL)
    counts = {}
    for path in Path("backend/pcba-erp-admin/target/surefire-reports").glob("TEST-*.xml"):
        suite = ET.parse(path).getroot()
        name = suite.attrib["name"].rsplit(".", 1)[-1]
        counts[name] = {key: int(suite.attrib.get(key, "0")) for key in ("tests", "failures", "errors", "skipped")}
    names = ("OrderWorkflowIntegrationTest", "PostgresqlIntegrationTest")
    if any(not counts.get(name, {}).get("tests") for name in names):
        raise RuntimeError("Expected integration suites did not run")
    if any(row[field] for row in counts.values() for field in ("failures", "errors", "skipped")):
        raise RuntimeError("Maven report contains failed, errored or skipped tests")
    print(f"MAVEN PASS total={sum(row['tests'] for row in counts.values())} "
          f"integration={sum(counts[name]['tests'] for name in names)} skipped=0")


def stage_cache():
    return Path(os.environ.get("AGENTCODE_STAGE_CACHE", str(Path.home() / ".agentcode" / "staging"))).resolve()


def pinned_source(project, expected):
    repo = Path.cwd()
    if output(["git", "status", "--porcelain"]):
        raise ValueError("Staging source must be a clean Git checkout")
    head = output(["git", "rev-parse", "HEAD"])
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", head) or head != expected:
        raise ValueError("Staging requires the expected Git commit at HEAD")
    cache = stage_cache()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    tree = Path(tempfile.mkdtemp(prefix=f"{project}-{head[:12]}-", dir=cache))
    archive = tree / "source.tar"
    run(["git", "archive", "--format=tar", "--output", str(archive), head], cwd=repo)
    with archive.open("rb") as stream:
        archived = run(["git", "get-tar-commit-id"], stdin=stream, stdout=subprocess.PIPE).stdout.decode().strip()
    if head != archived:
        raise RuntimeError("Git archive revision does not match the requested revision")
    src = tree / "source"
    src.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(src, filter="data")
    return head, src


def river_services(src, port):
    content = yaml.safe_load((src / "docker-compose.staging.yml").read_text(encoding="utf-8"))
    services = content["services"]
    services["web"]["ports"] = [f"127.0.0.1:{port}:80"]
    # The archived init.sql is mounted by a path visible to the Docker daemon.
    services["postgres"]["volumes"][0] = linux_path(src / "infra/postgres/init.sql") + ":/docker-entrypoint-initdb.d/001-init.sql:ro"
    services["postgres"]["volumes"].append("river-stage-data:/var/lib/postgresql/data")
    content.setdefault("volumes", {})["river-stage-data"] = {}
    services["postgres"]["image"] = image(services["postgres"]["image"])
    for name in ("api", "web"):
        services[name]["build"]["context"] = linux_path(src)
    if os.environ.get("AGENTCODE_REGISTRY_PREFIX"):
        dockerfile = src / "infra/docker/Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        dockerfile.write_text(re.sub(r"(?m)^FROM ([^/ \r\n]+)", lambda m: "FROM " + image(m[1]), text),
                              encoding="utf-8")
    return content, ("api", "web")


def proj_b_services(src, port):
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "ci", "--no-audit", "--no-fund"], cwd=src / "frontend")
    run([npm, "run", "build"], cwd=src / "frontend", env={**os.environ, "VITE_API_BASE": "/api"})
    maven("-DskipTests", "package", cwd=src)
    api = src / "backend/pcba-erp-admin/target"
    web = src.parent / "web-image"
    web.mkdir()
    shutil.copytree(src / "frontend/dist", web / "html")
    (api / "Dockerfile.agentcode").write_text(f"""FROM {image('eclipse-temurin:17-jre-alpine')}
WORKDIR /opt/pcba-erp
COPY --chown=65532:65532 pcba-erp.jar app.jar
USER 65532:65532
ENTRYPOINT ["java","-Xms192m","-Xmx768m","-jar","app.jar"]
""", encoding="utf-8")
    (web / "Dockerfile.agentcode").write_text(f"""FROM {image('nginx:1.27-alpine')}
COPY html/ /usr/share/nginx/html/
COPY agentcode-nginx.conf /etc/nginx/conf.d/default.conf
""", encoding="utf-8")
    (web / "agentcode-nginx.conf").write_text(
        "server { listen 80; location /api/ { proxy_pass http://backend:8080/; } "
        "location / { root /usr/share/nginx/html; try_files $uri $uri/ /index.html; } }",
        encoding="utf-8")
    db_password, redis_password, jwt, admin = (secrets.token_hex(n) for n in (16, 16, 32, 9))
    def health(command):
        return {"test": ["CMD-SHELL", command], "interval": "3s", "timeout": "3s", "retries": 45}

    def depends(*services):
        return {name: {"condition": "service_healthy"} for name in services}

    db = {"POSTGRES_DB": "staging", "POSTGRES_USER": "staging", "POSTGRES_PASSWORD": db_password}
    services = {
        "postgres": {"image": image("postgres:16-alpine"), "environment": db,
                     "tmpfs": ["/var/lib/postgresql/data"], "healthcheck": health("pg_isready -U staging -d staging")},
        "redis": {"image": image("redis:7.2.4-alpine"), "environment": {"REDIS_PASSWORD": redis_password},
                  "command": ["sh", "-ec", 'exec redis-server --requirepass "$${REDIS_PASSWORD}"'],
                  "healthcheck": health('REDISCLI_AUTH="$${REDIS_PASSWORD}" redis-cli ping | grep -qx PONG')},
        "backend": {"build": {"context": linux_path(api), "dockerfile": "Dockerfile.agentcode"},
                    "depends_on": depends("postgres", "redis"),
                    "environment": {**db, "DB_HOST": "postgres", "REDIS_HOST": "redis", "REDIS_PASSWORD": redis_password,
                                    "JWT_SECRET": jwt, "BOOTSTRAP_ADMIN_PASSWORD": admin,
                                    "CORS_ALLOWED_ORIGINS": f"http://127.0.0.1:{port}"},
                    "healthcheck": health("wget -qO- http://127.0.0.1:8080/actuator/health/readiness >/dev/null")},
        "frontend": {"build": {"context": linux_path(web), "dockerfile": "Dockerfile.agentcode"},
                     "depends_on": depends("backend"), "ports": [f"127.0.0.1:{port}:80"]},
    }
    return {"services": services}, ("backend", "frontend")


def http(base, path, data=None):
    request = Request(base + path, data=json.dumps(data).encode() if data else None,
                      headers={"Content-Type": "application/json"})
    with OPENER.open(request, timeout=8) as response:
        return response.read().decode("utf-8")


def staging(project, expected):
    _, port, ready = STAGING[project]
    prepare = {"river": river_services, "proj-b": proj_b_services}[project]
    head, src = pinned_source(project, expected)
    with running_wsl():
        config, names = prepare(src, port)
        stack = f"agentcode-{project}-staging"
        for name in names:
            service = config["services"][name]
            service["image"] = f"{stack}-{name}:{head}"
            service["build"].setdefault("labels", {}).update({
                "org.opencontainers.image.revision": head,
                "org.agentcode.staging.archive": src.parent.name,
            })
        manifest = src.parent / "compose.json"
        with os.fdopen(os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            json.dump(config, stream)
        compose = docker("compose", "-p", stack, "-f", linux_path(manifest))
        run([*compose, "up", "-d", "--build", "--wait", "--wait-timeout", "180"])
        image_ids = {}
        for name in names:
            container = output([*compose, "ps", "-q", name])
            image_id = output(docker("inspect", "--format", "{{.Image}}", container))
            inspected, = json.loads(output(docker("image", "inspect", image_id)))
            if inspected["Config"]["Labels"].get("org.opencontainers.image.revision") != head:
                raise RuntimeError(f"{name} is not running the archived commit")
            image_ids[name] = image_id
            print(f"STAGING {name} image={image_id} head={head}", flush=True)
        base = f"http://127.0.0.1:{port}"
        readiness = wait_until(lambda: json.loads(http(base, ready)))
        if project == "river":
            run([*WSL, "env", f"BASE={base}", "bash", linux_path(src / "infra/docker/staging-smoke.sh")])
        else:
            user = config["services"]["backend"]["environment"]["BOOTSTRAP_ADMIN_PASSWORD"]
            logged_in = json.loads(http(base, "/api/auth/login", {"username": " admin ", "password": user}))
            if (readiness.get("status") != "UP" or 'id="app"' not in http(base, "/login")
                    or logged_in.get("code") != 200 or logged_in.get("data", {}).get("username") != "admin"):
                raise RuntimeError("proj-b readiness or normalized login failed")
        verified = src.parent / "stage-verified.json"
        verified.write_text(json.dumps({"head": head, "images": image_ids,
                                       "manifest_sha256": sha256(manifest.read_bytes()).hexdigest()}), encoding="utf-8")
        print(f"STAGING PASS head={head} endpoint={base} manifest={manifest}")
        print("STAGING PROOF " + json.dumps({"head": head, "images": image_ids, "archive": src.parent.name}))


def current_release(project, expected=None):
    """Read the running pinned images, release proof and HTTP readiness without redeploying."""
    names, port, ready = STAGING[project]
    revisions, archives, image_ids = set(), set(), {}
    for name in names:
        container = f"agentcode-{project}-staging-{name}-1"
        image_id = output(docker("inspect", "--format", "{{.State.Running}} {{.Image}}", container)).split()
        if len(image_id) != 2 or image_id[0] != "true":
            raise RuntimeError(f"{container} is not running")
        image_ids[name] = image_id[1]
        item, = json.loads(output(docker("image", "inspect", image_id[1])))
        labels = item["Config"]["Labels"]
        revisions.add(labels["org.opencontainers.image.revision"])
        archives.add(labels["org.agentcode.staging.archive"])
    revision, = revisions
    archive, = archives
    if ((expected and revision != expected) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision)
            or not re.fullmatch(rf"{project}-{revision[:12]}-[a-z0-9_]+", archive)):
        raise RuntimeError("Running revision does not match the expected staging release")
    directory = stage_cache() / archive
    manifest, evidence = verified_manifest(directory, revision)
    if evidence["images"] != image_ids:
        raise RuntimeError("The running images differ from the verified release")
    health = json.loads(http(f"http://127.0.0.1:{port}", ready))
    healthy = {"version", "uptimeSeconds"} <= health.keys() if project == "river" else health.get("status") == "UP"
    if not healthy:
        raise RuntimeError("Running release is not ready")
    return {"head": revision, "images": image_ids, "archive": archive, "manifest": str(manifest)}


def verified_manifest(directory, head):
    manifest = directory / "compose.json"
    record = json.loads((directory / "stage-verified.json").read_text(encoding="utf-8"))
    if record["head"] != head or record["manifest_sha256"] != sha256(manifest.read_bytes()).hexdigest():
        raise RuntimeError("Saved deployment verification does not match the manifest")
    return manifest, record


def staging_proof(output_text, project, head):
    entries = [line.removeprefix("STAGING PROOF ") for line in output_text.splitlines()
               if line.startswith("STAGING PROOF ")]
    if len(entries) != 1:
        raise ValueError("部署输出缺少唯一的镜像凭证")
    proof = json.loads(entries[0])
    if (proof.get("head") != head or not re.fullmatch(rf"{project}-{head[:12]}-[a-z0-9_]+", proof.get("archive", ""))
            or set(proof.get("images", {})) != set(STAGING[project][0])
            or not all(re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) for image_id in proof["images"].values())):
        raise ValueError("部署镜像凭证与当前交付不符")
    return proof


def rollback_river(approved):
    """Restore the already-approved River images, with an unchanged database schema."""
    with running_wsl():
        observed = current_release("river")
        head = approved["head"]
        if all(observed[key] == approved[key] for key in ("head", "images", "archive")):
            return observed
        active = Path(observed["manifest"])
        target, evidence = verified_manifest(stage_cache() / approved["archive"], head)
        if evidence["images"] != approved["images"]:
            raise RuntimeError("Saved images differ from the G3 approval")
        def schema(manifest):
            return (manifest.parent / "source/infra/postgres/init.sql").read_bytes()
        if schema(active) != schema(target):
            raise RuntimeError("Database schema differs; rollback requires a migration decision")
        services = json.loads(target.read_text(encoding="utf-8"))["services"]
        for name, expected in evidence["images"].items():
            if output(docker("image", "inspect", "--format", "{{.Id}}", services[name]["image"])) != expected:
                raise RuntimeError("An approved target image is not available under the saved tag")
        run(docker("compose", "-p", "agentcode-river-staging", "-f", linux_path(target),
                   "up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "180"))
        restored = wait_until(lambda: current_release("river", head))
        if restored["images"] != approved["images"]:
            raise RuntimeError("Rollback images do not match the G3 approval")
        return restored


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "proj-b-integration":
        proj_b_integration()
    elif action == "proj-b-compile":
        maven("-DskipTests", "compile")
    elif action == "staging":
        staging(sys.argv[2], sys.argv[3])
    elif action == "check-staging":
        with running_wsl():
            observed = current_release(sys.argv[2], sys.argv[3])
            print("STAGING PROOF " + json.dumps({key: observed[key] for key in ("head", "archive", "images")}))
    else:
        raise SystemExit("Supported commands: proj-b-integration, proj-b-compile, staging river|proj-b <commit>")
