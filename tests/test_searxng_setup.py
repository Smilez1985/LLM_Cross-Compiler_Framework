"""The SearXNG of Ditto's web search, set up for the person (orchestrator/Core/searxng_setup.py): the finished settings file
is copied and never overwritten, the secret is made once for the person alone and the container is started on this computer
only. The container engine is a fake docker-py client; nothing is pulled."""

import os
import stat

import pytest
import yaml
from docker.errors import ImageNotFound, NotFound

from orchestrator.Core import searxng_setup as ss
from orchestrator.Core.searxng_setup import SetupError, ensure_secret, folder_of, prepare, setup


class FakeContainer:
    def __init__(self, status="created"):
        self.status = status
        self.started = self.removed = False

    def start(self):
        self.started, self.status = True, "running"

    def remove(self, force=False):
        self.removed = True


class FakeClient:
    """docker-py's client as far as the setup uses it."""

    def __init__(self, existing=None, images=("have",), pull_error=None, start_error=None):
        self.container = existing
        self.known_images = set(images)
        self.pulled, self.created = [], []
        self.pull_error, self.start_error = pull_error, start_error
        outer = self

        class Containers:
            def get(self, name):
                if outer.container is None:
                    raise NotFound("no such container")
                return outer.container

            def create(self, **kwargs):
                outer.created.append(kwargs)
                outer.container = FakeContainer()
                if outer.start_error:
                    err = outer.start_error
                    outer.container.start = lambda: (_ for _ in ()).throw(err)
                return outer.container

        class Images:
            def get(self, name):
                if name not in outer.known_images:
                    raise ImageNotFound("no image")

            def pull(self, name):
                if outer.pull_error:
                    raise outer.pull_error
                outer.pulled.append(name)
                outer.known_images.add(name)

        self.containers, self.images = Containers(), Images()
        self.api = type("A", (), {"_version": "1.43"})()


@pytest.fixture(autouse=True)
def plain_engine(monkeypatch):
    """create_container is the engine module's; with a fake client it must just call client.containers.create."""
    from orchestrator.utils import docker_engine
    monkeypatch.setattr(docker_engine, "rootless_podman", lambda client: False)
    monkeypatch.setattr(docker_engine, "rootless_docker", lambda client: False)


class TestTheShippedFile:

    def test_the_finished_file_turns_json_on_and_holds_no_secret(self):
        data = yaml.safe_load(ss.TEMPLATE.read_text(encoding="utf-8"))
        assert data["use_default_settings"] is True
        assert data["search"]["formats"] == ["html", "json"]
        assert "secret_key" not in data.get("server", {})        # one key in a public repository would be everybody's
        assert data["server"]["limiter"] is False and data["server"]["public_instance"] is False

    def test_the_default_address_of_the_web_search_is_where_the_container_is_published(self):
        from orchestrator.Core.web_search import DEFAULT_URL
        assert ss.URL == DEFAULT_URL == "http://127.0.0.1:8888"


class TestTheSettingsFile:

    def test_it_is_copied_as_it_is(self, tmp_path):
        target, copied = prepare(folder_of(tmp_path))
        assert copied and target == tmp_path / "searxng" / "config" / "settings.yml"
        assert target.read_bytes() == ss.TEMPLATE.read_bytes()

    def test_a_file_that_is_there_is_never_overwritten(self, tmp_path):
        target, _ = prepare(folder_of(tmp_path))
        target.write_text("use_default_settings: true\n# the person's own\n", encoding="utf-8")
        again, copied = prepare(folder_of(tmp_path))
        assert not copied and "the person's own" in again.read_text(encoding="utf-8")

    def test_a_missing_template_is_told_in_plain_words(self, tmp_path):
        with pytest.raises(SetupError) as e:
            prepare(folder_of(tmp_path), template=tmp_path / "none.yml")
        assert "install the framework again" in str(e.value)


class TestTheSecret:

    def test_it_is_made_once_and_kept(self, tmp_path):
        folder = folder_of(tmp_path)
        first, made = ensure_secret(folder)
        again, made_again = ensure_secret(folder)
        assert made and not made_again and first == again and len(first) == 64

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_only_the_person_can_read_it(self, tmp_path):
        ensure_secret(folder_of(tmp_path))
        assert stat.S_IMODE((tmp_path / "searxng" / "secret").stat().st_mode) == 0o600

    def test_every_computer_gets_its_own(self, tmp_path):
        assert ensure_secret(folder_of(tmp_path / "a"))[0] != ensure_secret(folder_of(tmp_path / "b"))[0]

    def test_a_short_or_damaged_file_is_replaced(self, tmp_path):
        folder = folder_of(tmp_path)
        folder.mkdir(parents=True)
        (folder / "secret").write_text("short", encoding="ascii")
        secret, made = ensure_secret(folder)
        assert made and len(secret) == 64


class TestStarting:

    def test_the_container_is_made_with_the_settings_the_secret_and_the_port_on_this_computer_only(self, tmp_path):
        client = FakeClient()
        result = setup(tmp_path, lambda: client)
        made = client.created[0]
        assert made["image"] == ss.IMAGE and made["name"] == ss.CONTAINER
        assert made["ports"] == {"8080/tcp": ("127.0.0.1", 8888)}                # never on every address
        assert made["environment"]["SEARXNG_SECRET"] == (tmp_path / "searxng" / "secret").read_text().strip()
        binds = {v["bind"]: k for k, v in made["volumes"].items()}
        assert binds["/etc/searxng"] == str(tmp_path / "searxng" / "config") and binds["/var/cache/searxng"] == ss.VOLUME
        assert made["restart_policy"] == {"Name": "unless-stopped"}
        assert result.started and result.running and result.copied and result.secret_made and client.container.started

    def test_the_image_is_pulled_only_when_the_engine_has_none(self, tmp_path):
        client = FakeClient(images=())
        setup(tmp_path, lambda: client)
        assert client.pulled == [ss.IMAGE]
        again = FakeClient(images=(ss.IMAGE,))
        setup(tmp_path / "b", lambda: again)
        assert again.pulled == []

    def test_a_running_container_is_left_alone(self, tmp_path):
        client = FakeClient(existing=FakeContainer("running"))
        result = setup(tmp_path, lambda: client)
        assert not result.started and result.running and client.created == []

    def test_a_stopped_container_is_started_again(self, tmp_path):
        stopped = FakeContainer("exited")
        result = setup(tmp_path, lambda: FakeClient(existing=stopped))
        assert result.started and stopped.started

    def test_a_second_run_changes_nothing(self, tmp_path):
        client = FakeClient()
        setup(tmp_path, lambda: client)
        again = setup(tmp_path, lambda: client)
        assert not again.copied and not again.secret_made and not again.started and len(client.created) == 1

    def test_without_run_only_the_file_and_the_secret_are_made(self, tmp_path):
        result = setup(tmp_path, lambda: pytest.fail("no engine without run"), run=False)
        assert result.copied and result.secret_made and not result.running

    def test_no_engine_is_one_plain_sentence_and_the_file_is_ready(self, tmp_path):
        def none():
            raise RuntimeError("Cannot connect to the Docker daemon")
        with pytest.raises(SetupError) as e:
            setup(tmp_path, none)
        assert "No container engine is running" in str(e.value) and "Cannot connect" not in str(e.value)
        assert (tmp_path / "searxng" / "config" / "settings.yml").is_file() and (tmp_path / "searxng" / "secret").is_file()

    def test_a_port_in_use_says_which_port(self, tmp_path):
        client = FakeClient(start_error=RuntimeError("Bind for 127.0.0.1:8888 failed: port is already allocated"))
        with pytest.raises(SetupError) as e:
            setup(tmp_path, lambda: client)
        assert "Port 8888" in str(e.value)

    def test_a_failed_pull_is_told_without_a_traceback(self, tmp_path):
        client = FakeClient(images=(), pull_error=RuntimeError("no route to host\nmore lines"))
        with pytest.raises(SetupError) as e:
            setup(tmp_path, lambda: client)
        assert "refused it: no route to host" in str(e.value) and "more lines" not in str(e.value)


class TestStopping:

    def test_it_removes_the_container_and_keeps_the_rest(self, tmp_path):
        client = FakeClient()
        setup(tmp_path, lambda: client)
        assert ss.stop(lambda: client) == "removed" and client.container.removed
        assert (tmp_path / "searxng" / "secret").is_file()

    def test_nothing_to_remove_is_said(self):
        assert ss.stop(lambda: FakeClient()) == "missing"
