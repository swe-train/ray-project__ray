import time

from ray_release.exception import AppConfigBuildFailure
from ray_release.logger import logger
from ray_release.session_manager.session_manager import SessionManager
from ray_release.util import format_link, anyscale_app_config_build_url

REPORT_S = 30.


class MinimalSessionManager(SessionManager):
    """Minimal manager.

    Builds app config and compute template but does not start or stop session.
    """

    def create_cluster_env(self, _repeat: bool = True):
        assert self.cluster_env_id is None

        if self.cluster_env:
            assert self.cluster_env_name

            logger.info(f"Test uses a cluster env with name "
                        f"{self.cluster_env_name}. Looking up existing "
                        f"cluster envs with this name.")

            paging_token = None
            while not self.cluster_env_id:
                result = self.sdk.search_cluster_environments(
                    dict(
                        project_id=self.project_id,
                        name=dict(equals=self.cluster_env_name),
                        paging=dict(count=50, token=paging_token)))
                paging_token = result.metadata.next_paging_token

                for res in result.results:
                    if res.name == self.cluster_env_name:
                        self.cluster_env_id = res.id
                        logger.info(f"Cluster env already exists with ID "
                                    f"{self.cluster_env_id}")
                        break

                if not paging_token or self.cluster_env_id:
                    break

            if not self.cluster_env_id:
                logger.info("Cluster env not found. Creating new one.")
                try:
                    result = self.sdk.create_cluster_environment(
                        dict(
                            name=self.cluster_env_name,
                            project_id=self.project_id,
                            config_json=self.cluster_env))
                    self.cluster_env_id = result.result.id
                except Exception as e:
                    if _repeat:
                        logger.warning(
                            f"Got exception when trying to create cluster "
                            f"env: {e}. Sleeping for 10 seconds and then "
                            f"try again once...")
                        time.sleep(10)
                        return self.create_cluster_env(_repeat=False)

                    raise e

                logger.info(
                    f"Cluster env created with ID {self.cluster_env_id}")

    def build_cluster_env(self, timeout: float = 600.):
        assert self.cluster_env_id
        assert self.cluster_env_build_id is None

        # Fetch build
        build_id = None
        last_status = None
        result = self.sdk.list_cluster_environment_builds(self.cluster_env_id)
        for build in sorted(result.results, key=lambda b: b.created_at):
            build_id = build.id
            last_status = build.status

            if build.status == "failed":
                continue

            if build.status == "succeeded":
                logger.info(
                    f"Link to cluster env build: "
                    f"{format_link(anyscale_app_config_build_url(build_id))}")
                self.cluster_env_build_id = build_id
                return

        if last_status == "failed":
            raise AppConfigBuildFailure("App config build failed.")

        if not build_id:
            raise AppConfigBuildFailure("No build found for app config.")

        # Build found but not failed/finished yet
        completed = False
        start_wait = time.time()
        next_report = start_wait + REPORT_S
        timeout_at = time.monotonic() + timeout
        logger.info(f"Waiting for build {build_id} to finish...")
        logger.info(f"Track progress here: "
                    f"{format_link(anyscale_app_config_build_url(build_id))}")
        while not completed:
            now = time.time()
            if now > next_report:
                logger.info(
                    f"... still waiting for build {build_id} to finish "
                    f"({int(now - start_wait)} seconds) ...")
                next_report = next_report + REPORT_S

            result = self.sdk.get_build(build_id)
            build = result.result

            if build.status == "failed":
                raise AppConfigBuildFailure(
                    f"Cluster env build failed. Please see "
                    f"{anyscale_app_config_build_url(build_id)} for details")

            if build.status == "succeeded":
                logger.info("Build succeeded.")
                self.cluster_env_build_id = build_id
                return

            completed = build.status not in ["in_progress", "pending"]

            if completed:
                raise AppConfigBuildFailure(
                    f"Unknown build status: {build.status}. Please see "
                    f"{anyscale_app_config_build_url(build_id)} for details")

            if time.monotonic() > timeout_at:
                raise AppConfigBuildFailure(
                    f"Time out when building cluster env {self.cluster_env_name}"
                )

            time.sleep(1)

        self.cluster_env_build_id = build_id

    def create_cluster_compute(self, _repeat: bool = True):
        assert self.cluster_compute_id is None

        if self.cluster_compute:
            assert self.cluster_compute

            logger.info(f"Tests uses compute template "
                        f"with name {self.cluster_compute_name}. "
                        f"Looking up existing cluster computes.")

            paging_token = None
            while not self.cluster_compute_id:
                result = self.sdk.search_cluster_computes(
                    dict(
                        project_id=self.project_id,
                        name=dict(equals=self.cluster_compute_name),
                        include_anonymous=True,
                        paging=dict(token=paging_token)))
                paging_token = result.metadata.next_paging_token

                for res in result.results:
                    if res.name == self.cluster_compute_name:
                        self.cluster_compute_id = res.id
                        logger.info(f"Cluster compute already exists "
                                    f"with ID {self.cluster_compute_id}")
                        break

                if not paging_token:
                    break

            if not self.cluster_compute_id:
                logger.info(f"Cluster compute not found. "
                            f"Creating with name {self.cluster_compute_name}.")
                try:
                    result = self.sdk.create_cluster_compute(
                        dict(
                            name=self.cluster_compute_name,
                            project_id=self.project_id,
                            config=self.cluster_compute))
                    self.cluster_compute_id = result.result.id
                except Exception as e:
                    if _repeat:
                        logger.warning(
                            f"Got exception when trying to create cluster "
                            f"compute: {e}. Sleeping for 10 seconds and then "
                            f"try again once...")
                        time.sleep(10)
                        return self.create_cluster_compute(_repeat=False)

                    raise e

                logger.info(f"Cluster compute template created with "
                            f"name {self.cluster_compute_name} and "
                            f"ID {self.cluster_compute_id}")

    def build_configs(self, timeout: float = 30.):
        self.create_cluster_compute()
        self.create_cluster_env()
        self.build_cluster_env(timeout=timeout)

    def delete_configs(self):
        if self.cluster_id:
            self.sdk.delete_cluster(self.cluster_id)
        if self.cluster_env_build_id:
            self.sdk.delete_cluster_environment_build(
                self.cluster_env_build_id)
        if self.cluster_env_id:
            self.sdk.delete_cluster_environment(self.cluster_env_id)
        if self.cluster_compute_id:
            self.sdk.delete_cluster_compute(self.cluster_compute_id)

    def start_cluster(self):
        pass

    def terminate_cluster(self):
        pass
