from pathlib import Path
from urllib.parse import urlencode

import click
import toml
from decouple import config
from requests.exceptions import HTTPError
import requests


DEBUG = config("DEBUG", default=False)
if DEBUG:
    # Temporary for hacking. Just prevents the same URL to be downloaded twice.
    import requests_cache

    requests_cache.install_cache(
        "requests_cache1", expire_after=60 * 5, allowable_methods=["GET", "PUT"]
    )
    print(
        "Warning! Running in debug mode means all HTTP requests are cached "
        "indefinitely. To reset HTTP caches, delete the file 'requests_cache1.sqlite'"
    )


GITHUB_ACCESS_TOKEN = config("GITHUB_ACCESS_TOKEN")

BASE_URL = "https://api.github.com"

session = requests.Session()


def make_request(url, params=None, method="GET"):

    headers = {"Authorization": f"token {GITHUB_ACCESS_TOKEN}"}
    if BASE_URL not in url:
        url = BASE_URL + url
    full_url = url
    if method == "GET":
        if params:
            full_url += "?" + urlencode(params)
        response = session.get(full_url, headers=headers)
    elif method == "PUT" or method == "POST":
        params = params or {}
        func = session.post if method == "POST" else session.put
        response = func(full_url, json=params, headers=headers)
    else:
        raise NotImplementedError(method)
    response.raise_for_status()
    return response.json()


def download_request(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.text


def find_in_repo(owner, repo, verbose=False, dry_run=False, **options):

    # Check that the repo exists at all!
    repo_response = make_request(f"/repos/{owner}/{repo}")
    if verbose:
        print(f"Found repo {repo_response['full_name']}")

    # Defaults
    by_bors = False
    only_one = options.get("only_one", None)
    requires_approval = options.get("requires_approval", None)

    # Download the repos branch protections
    main_branch = options.get("main_branch", repo_response["default_branch"])
    try:
        protections = make_request(
            f"/repos/{owner}/{repo}/branches/{main_branch}/protection"
        )
        if "bors" in protections["required_status_checks"]["contexts"]:
            by_bors = True

        if only_one is None:
            only_one = protections["required_status_checks"]["strict"]

        if requires_approval is None:
            requires_approval = bool(protections.get("required_pull_request_reviews"))

    except HTTPError as exception:
        if exception.response.status_code == 404:
            if verbose:
                print("Repo has no branch protections. That's fine.")
        else:
            raise

    merge_method = options.get("merge_method", "bors" if by_bors else "merge")
    if by_bors:
        if merge_method != "bors":
            raise ValueError(
                "According to branch protections this repo requires 'bors' comments "
                f"which is not compatible with {merge_method!r}. "
            )

    if not requires_approval and by_bors:
        bors_toml = make_request(f"/repos/{owner}/{repo}/contents/bors.toml")
        content = download_request(bors_toml["download_url"])
        bors_parsed = toml.loads(content)
        required_approvals = bors_parsed.get("required_approvals", 0)
        requires_approval = required_approvals > 0

    if verbose:
        print("Repo merge strategy...")
        print("\tMerge method:".ljust(30), merge_method)
        print("\tBy 'bors':".ljust(30), by_bors)
        print("\tOne merge at a time:".ljust(30), only_one)
        print("\tRequires review approval:".ljust(30), requires_approval)
        print()

    # The sort means those least recently updated first.
    prs = make_request(
        f"/repos/{owner}/{repo}/pulls", {"state": "open", "sort": "updated"}
    )

    def repr_pr(p):
        return f"{p['title']!r} by {p['user']['login']}"

    def debug_pr(p):
        import json

        as_json_string = json.dumps(p, indent=3)
        print(as_json_string)

    def reject_pr(pr, reason=""):
        if reason and not (reason.startswith("(") and reason.endsswith(")")):
            reason = f"({reason})"
        html_url = pr["_links"]["html"]["href"]
        print(html_url, "is NOT mergeable", reason, "😢", repr_pr(full_pr))

    def make_bors_rplus_comment():
        return "bors r+\n\n(made with 'justmerge')\n"

    for pr in prs:
        if pr["locked"]:
            continue
        assert pr["state"] == "open"
        html_url = pr["_links"]["html"]["href"]
        full_pr = make_request(pr["url"])
        assert full_pr["state"] == "open", full_pr["state"]
        if not full_pr["mergeable"]:
            if verbose:
                # Can we figure out why?
                # Check if it has conflicts
                # debug_pr(full_pr)
                reason = ""
                if full_pr.get("mergeable_state") == "dirty":
                    reason = "Dirty!"
                reject_pr(full_pr, reason)
            continue

        if full_pr["mergeable_state"] != "clean":
            statuses = make_request(pr["_links"]["statuses"]["href"])
            status_states = {}
            for status in statuses:
                if status["context"] not in status_states:
                    status_states[status["context"]] = status["state"]

            ignore_blocked = False
            if full_pr["mergeable_state"] == "blocked":
                # Need to figure out if that's because it's blocked by the need of a
                # bors comment
                if all([x == "success" for x in status_states.values()]) and by_bors:
                    ignore_blocked = True
                    # if verbose:
                    #     print("Mergeable state is 'blocked' but proceeded with 'bors'")

            if not ignore_blocked:
                if verbose:
                    reason = f"mergeable state: {full_pr['mergeable_state']!r}"
                    for key, value in status_states.items():
                        if value != "success":
                            reason += f" [{key}={value}]"
                    reject_pr(full_pr, reason)
                continue

        exclusion_labels = options.get(
            "exclusion_labels", ["dontmerge", "bors-dont-merge"]
        )
        if exclusion_labels:
            if not isinstance(exclusion_labels, (list, tuple)):
                exclusion_labels = [exclusion_labels]
            _dont = False
            for label in full_pr.get("labels", []):
                if label["name"] in exclusion_labels:
                    # doit = False
                    if verbose:
                        reject_pr(full_pr, f"exclusion label {label['name']!r}")
                    _dont = True
                    break
            if _dont:
                continue

        inclusion_users = options.get("inclusion_users", ["renovate", "pyup-bot"])
        if inclusion_users:
            if not isinstance(inclusion_users, (list, tuple)):
                inclusion_users = [inclusion_users]
            inclusion_users = set(inclusion_users)
            login = full_pr["user"]["login"]
            logins = set([login])
            if login.endswith("[bot]"):
                logins.add(login.replace("[bot]", ""))

            if not logins & inclusion_users:
                if verbose:
                    reject_pr(full_pr, f"exclusion user {login} != {inclusion_users}")
                continue

        print(html_url, "is", "✅", repr_pr(full_pr))

        if dry_run:
            print("Dry run. Not doing anything")
            continue

        merge_url = pr["url"] + "/merge"
        if merge_method == "bors":
            reviews_url = pr["url"] + "/reviews"
            reviews = make_request(reviews_url)
            if reviews:
                debug_pr(full_pr)
                raise NotImplementedError(
                    "Need to check that there isn't already a bors review of this one"
                )
            if requires_approval:
                response = make_request(
                    reviews_url,
                    {
                        "commit_id": full_pr["head"]["sha"],
                        "body": make_bors_rplus_comment(),
                        "event": "APPROVE",
                    },
                    method="POST",
                )
                print("Bors approved!! 🎉 🎊 🤖", repr_pr(full_pr))
                if verbose:
                    print("\tApproval comment URL:", response["_links"]["html"]["href"])
                    # print(response)
            else:
                # Just a plain comment is enough.
                # Need to check if there already is a "bors r+" comment.
                comments_url = full_pr["_links"]["comments"]["href"]
                comments = make_request(comments_url)
                chicken_out = False
                for comment in comments:
                    if "bors r+" in comment["body"]:
                        chicken_out = True
                        print("⚠️", repr_pr(full_pr), "already has a 'bors r+' comment")
                if chicken_out:
                    continue
                response = make_request(
                    comments_url, {"body": make_bors_rplus_comment()}, method="POST"
                )
                print("Bors commented!! 🎉 🎊 🤖 ☄️", repr_pr(full_pr))
                if verbose:
                    print("\tComment URL:", response["html_url"])

        elif merge_method in ("merge", "squash", "rebase"):
            if requires_approval:
                # Need to make an approval first, then press the green button.
                raise NotImplementedError()
            # Then good old green button!
            try:
                merged_response = make_request(
                    merge_url,
                    {
                        "merge_method": merge_method,
                        "commit_title": full_pr["title"],
                        "commit_message": full_pr["body"],
                        "sha": full_pr["head"]["sha"],
                    },
                    method="PUT",
                )
                print("Merged! 🎉 🎊 🚀", repr_pr(full_pr))
                if verbose:
                    print(merged_response)
            except HTTPError as exception:
                if exception.response.status_code == 405:
                    # https://developer.github.com/v3/pulls/#response-if-merge-cannot-be-performed
                    print("🚨 405 error from GitHub. Perhaps cached incorrectly.")
                else:
                    # print(exception.response)
                    raise
        else:
            raise NotImplementedError(merge_method)

        # If you have the repo set up with
        # the "Require branches to be up to date before merging" option,
        # after 1 PR has been merge, automatically all other PRs will be out of
        # date.
        if only_one:
            if verbose:
                print()
                print("But, remember... Only 1 merge at a time❗️")
                print()
            break


def run_config_file(config, verbose=False, dry_run=False):
    if isinstance(config, Path):
        with config.open() as f:
            repo = toml.load(f)
    else:
        repo = toml.load(config)

    try:
        assert repo["owner"]
        assert repo["repo"]
    except (AssertionError, KeyError) as exc:
        error_out(f"Missing config key: {exc}")

    # The config might explicitly dictate to be verbose even if the cli
    # flag for 'verbose' wasn't set.
    verbose = verbose or repo.pop("verbose", False)
    find_in_repo(
        repo.pop("owner"), repo.pop("repo"), verbose=verbose, dry_run=dry_run, **repo
    )


def error_out(msg, raise_abort=True):
    click.echo(click.style(msg, fg="red"))
    if raise_abort:
        raise click.Abort


@click.command()
@click.option("-v", "--verbose", is_flag=True)
@click.option("-d", "--dry-run", is_flag=True)
@click.option("-a", "--all", is_flag=True)
@click.argument("configfile", type=click.File("r"), nargs=-1)
def cli(configfile, all, dry_run, verbose):
    if not all and not configfile:
        error_out("No config files provided and --all not used.")
    if all and configfile:
        error_out("Make up your mind. Either use --all or specify files by name.")
    if all:
        folder = Path("conf.d")
        if not folder.is_dir():
            folder.mkdir()
        configfile = list(folder.glob("*.toml"))
        if not configfile:
            error_out(f"{folder} is empty.")

    for i, conf in enumerate(configfile):
        if i > 0:
            print("\n")
        run_config_file(conf, verbose=verbose, dry_run=dry_run)
