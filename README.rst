=========
justmerge
=========

Helping you find GitHub Pull Requests that are ready to merge and do so.

⚠️EXPERIMENTAL!️️⚠️

About
-----

It uses the GitHub v3 API to figure out which pull requests are ready to
merge and then just merges them. There are many options that filters and
prevents etc. but one of the most important is that it has a list of
"inclusive users" and by default these are ``pyup`` and ``renovate``.
Any ready-to-merge pull requests made by any other users will be ignored
and left to manual scrutiny.

How to use
----------

You need a ``.toml`` file. There's a `sample one that looks like this
<https://github.com/peterbe/justmerge/blob/master/conf.d/myproject.toml.sample>`_.

With your ``my.toml`` file ready, run ``justmerge my.toml``. But first
you need `a GitHub Personal access token <https://github.com/settings/tokens>`_.
Generate a new one and either use it as an environment variable...::

    GITHUB_ACCESS_TOKEN=f011af46d1cae0879b150b174af4c081167313456 justmerge my.toml

Or, put it into a ``.env`` file.

Configuration
-------------

**NEEDS MORE WORK**
