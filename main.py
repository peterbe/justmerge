from urllib.parse import urlencode

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

    # Download the repos branch protections
    main_branch = options.get("main_branch", "master")
    protections = make_request(
        f"/repos/{owner}/{repo}/branches/{main_branch}/protection"
    )
    by_bors = False
    if "bors" in protections["required_status_checks"]["contexts"]:
        by_bors = True

    only_one = options.get("only_one", None)
    if only_one is None:
        only_one = protections["required_status_checks"]["strict"]

    merge_method = options.get("merge_method", "bors" if by_bors else "merge")
    if by_bors:
        if merge_method != "bors":
            raise ValueError(
                "According to branch protections this repo requires 'bors' comments "
                f"which is not compatible with {merge_method!r}. "
            )

    # I wish there was a better way to find this out! Probably is.
    requires_approval = options.get("requires_approval", None)
    if requires_approval is None:

        requires_approval = bool(protections.get("required_pull_request_reviews"))

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
            ignore_blocked = False
            if full_pr["mergeable_state"] == "blocked":
                # Need to figure out if that's because it's blocked by the need of a
                # bors comment.
                statuses = make_request(pr["_links"]["statuses"]["href"])
                status_states = {}
                for status in statuses:
                    if status["context"] not in status_states:
                        status_states[status["context"]] = status["state"]
                # from pprint import pprint

                # pprint(status_states)
                if all([x == "success" for x in status_states.values()]) and by_bors:
                    ignore_blocked = True
                    if verbose:
                        print("Mergeable state is 'blocked' but proceeded with 'bors'")

            if not ignore_blocked:
                if verbose:
                    reject_pr(
                        full_pr, f"mergeable state: {full_pr['mergeable_state']!r}"
                    )
                debug_pr(full_pr)

                continue
        # Check if it requires approval and that it has been approved.
        # XXX WORK HARDER

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

        inclusion_users = options.get("inclusion_users")
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
                # comments_url =
                # debug_pr(full_pr)
                # Need to check if there already is a "bors r+" comment.
                comments_url = full_pr["_links"]["comments"]["href"]
                comments = make_request(comments_url)
                chicken_out = False
                # print(comments)
                for comment in comments:
                    if "bors r+" in comment["body"]:
                        chicken_out = True
                        print("⚠️", repr_pr(full_pr), "already has a 'bors r+' comment")
                    # else:
                    #     from pprint import pprint

                    #     pprint(comment)
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


def main():

    # find_in_repo("peterbe", "gg", merge_method="squash")

    # find_in_repo(
    #     "peterbe",
    #     "django-peterbecom",
    #     verbose=1,
    #     merge_method="squash",
    #     exclusion_labels="dontmerge",
    #     inclusion_users="pyup-bot",
    # )

    # find_in_repo(
    #     "mozilla-services",
    #     "tecken",
    #     verbose=1,
    #     merge_method="squash",
    #     exclusion_labels="dontmerge",
    #     inclusion_users=["renovate", "pyup-bot"],
    #     # only_one=True,
    # )

    find_in_repo(
        "mozilla",
        "buildhub2",
        verbose=1,
        # merge_method="bors_comment",
        # merge_method="bors_comment",
        exclusion_labels="dontmerge",
        inclusion_users=["renovate", "pyup-bot"],
        only_one=1,  # TEMPORARY SO IT DOESN'T MERGE TOO MANY!
    )


if __name__ == "__main__":
    # XXX switch to Click
    main()
