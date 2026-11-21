---
title: The tools a model may call
type: reference
audience: [researcher, scenario-author, developer, reader]
applies_to: {platform: ">=0.0"}
last_verified: {date: null, rc: null, by: null}
---

<!-- Written by `make tools` from tools/registry/. Do not edit it by hand. -->

# The tools a model may call

Version 1 of the tool registry, content hash `df636411c5bd3c0c6a38558310f6896e0c077bedb78386badb03e38c3ebe68fa`.

A tool runs one command, element by element as its entry writes it and never through a shell, or is carried out by a handler of the executing service. Some tools run a second command afterwards, which shows what the first one did. In a command, a name in braces is the argument of that name, and an element written as `{"literal": ...}` is taken as it is, braces and all.

A call's arguments are checked against the rules below and against the run's nodes before anything runs. A string and a path are printable ASCII, of at most 1024 and 4096 characters unless a rule says fewer. A value that is a whole element of a command is not empty and does not begin with a dash. No path lies in `/dev`, `/proc` or `/sys`, whatever else allows it. A mutating call also carries `_meta`: the model's confidence in the action, its reason and the effect it expects.

## Read-only tools

### `check_disk_usage`

Show how full each file system of a node is.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["df", "--human-readable", "--output=source,fstype,size,used,avail,pcent,target"]`. Ended after 15 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `check_inodes`

Show how many inodes each file system of a node has used and has left.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["df", "--output=source,fstype,itotal,iused,iavail,ipcent,target"]`. Ended after 15 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `check_memory`

Show a node's memory and swap, in mebibytes.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["free", "--mebi", "--wide"]`. Ended after 10 s and killed 5 s later; at most 4096 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `dns_lookup`

Ask a node's resolver for a host name and show its answer.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["dig", "-r", "+timeout=2", "+tries=1", "-q", "{name}"]`. Ended after 15 s and killed 5 s later; at most 8192 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `name` | string | yes | matches `^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$` | The host name: lowercase letters, digits, dots and hyphens. |
| `node` | string | yes | one of the run's nodes | The node that asks. |

### `docker_inspect`

Show a container's state, image, restart policy and restart count.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["docker", "inspect", "--type=container", {"literal": "--format={{json .State}} image={{.Config.Image}} restart={{.HostConfig.RestartPolicy.Name}} restarts={{.RestartCount}}"}, "{container}"]`. Ended after 20 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `container` | string | yes | matches `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` | The container's name or ID, as docker_ps shows it. |
| `node` | string | yes | one of the run's nodes | The node. |

### `docker_logs`

Show the last log lines of a container on a node.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["docker", "logs", "--tail={lines}", "{container}"]`. Ended after 20 s and killed 5 s later; at most 65536 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `container` | string | yes | matches `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` | The container's name or ID, as docker_ps shows it. |
| `lines` | integer | no; default `100` | from 1 to 500 | How many lines, counted from the newest. |
| `node` | string | yes | one of the run's nodes | The node. |

### `docker_ps`

List a node's containers, the stopped ones too, with their state.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["docker", "ps", "--all"]`. Ended after 20 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `get_service_status`

Show a systemd service's state on a node, with its last log lines.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["systemctl", "status", "--no-pager", "--full", "--lines=20", "{unit}"]`. Ended after 15 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\.service$` | The service's unit, as list_nodes names it. |

### `http_probe`

Send an HTTP request to one of a node's endpoints from the probe client, and show the status, the headers and the start of the body.

- Risk: read. Version of the entry: 1.
- Runs on the run's probe client: `["curl", "--disable", "--globoff", "--silent", "--show-error", "--insecure", "--include", "--max-time", "10", "{endpoint}"]`. Ended after 20 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `endpoint` | string | yes | an HTTP endpoint of the node, by its name | The endpoint's name, as list_nodes names it. |
| `node` | string | yes | one of the run's nodes | The node whose endpoint is asked. |

### `list_dir`

List a directory of a node, hidden entries included, with sizes, owners and modes.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["ls", "-l", "--all", "--time-style=long-iso", "--", "{path}"]`. Ended after 15 s and killed 5 s later; at most 32768 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `path` | path | yes | a path of the node that may be read | The directory's absolute path, one of the readable paths or below one. |

### `list_failed_units`

List the systemd units of a node that are in the failed state.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["systemctl", "list-units", "--state=failed", "--no-pager", "--plain"]`. Ended after 15 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `list_listening_ports`

List the TCP and UDP ports a node listens on, with the process behind each.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["ss", "--listening", "--tcp", "--udp", "--numeric", "--processes"]`. Ended after 10 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `list_nodes`

List the nodes of the system: their names and the services, paths and endpoints you may ask about.

- Risk: read. Version of the entry: 1.
- Carried out by the handler `list_nodes`; at most 16384 bytes are given back.

No arguments.

### `list_processes`

List a node's processes, the heaviest first by CPU or by memory. The list is cut after its first lines.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["ps", "-e", "-o", "pid,ppid,user,pcpu,pmem,rss,stat,etime,args", "--sort=-%{by}", "--cols=160"]`. Ended after 15 s and killed 5 s later; at most 8192 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `by` | string | no; default `cpu` | one of `cpu`, `mem` | What to sort by: cpu or mem. |
| `node` | string | yes | one of the run's nodes | The node. |

### `network_interfaces`

Show a node's network interfaces with their state and addresses.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["ip", "-brief", "address", "show"]`. Ended after 10 s and killed 5 s later; at most 8192 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `nginx_test_config`

Check nginx's configuration on a node without loading it, and show what nginx says. nginx -t may create or change files in nginx's own runtime directories.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["nginx", "-t"]`. Ended after 15 s and killed 5 s later; at most 8192 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `pg_activity_summary`

Count PostgreSQL's connections on a node by state, beside the most it allows.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["runuser", "-u", "postgres", "--", "psql", "--no-psqlrc", "--no-align", "--command=SELECT coalesce(state, 'none') AS state, count(*) AS connections, current_setting('max_connections') AS max_connections FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC"]`. Ended after 20 s and killed 5 s later; at most 8192 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `pg_isready`

Ask whether PostgreSQL on a node accepts connections.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["pg_isready"]`. Ended after 15 s and killed 5 s later; at most 4096 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `query_logs`

Search the collected logs of a service on a node.

- Risk: read. Version of the entry: 1.
- Carried out by the handler `query_logs`; at most 65536 bytes are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `level` | string | no; default `warning` | one of `error`, `warning`, `info` | The least severe level to show. |
| `node` | string | yes | one of the run's nodes | The node. |
| `service` | string | yes | a service of the node | The service's unit. |
| `since` | string | no; default `1h` | matches `^[0-9]{1,4}(s\|m\|h\|d)$` | How far back to look: a number and s, m, h or d, such as 15m. |

### `query_metrics`

Show how one measure of a node developed over the last while.

- Risk: read. Version of the entry: 1.
- Carried out by the handler `query_metrics`; at most 16384 bytes are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `metric` | string | yes | one of `cpu_usage`, `memory_usage`, `disk_usage` | The measure. |
| `node` | string | yes | one of the run's nodes | The node. |
| `since` | string | no; default `1h` | matches `^[0-9]{1,4}(s\|m\|h\|d)$` | How far back to look: a number and s, m, h or d, such as 15m. |

### `read_file`

Read a text file of a node. Only the paths list_nodes names as readable can be read.

- Risk: read. Version of the entry: 1.
- Carried out by the handler `read_file`; at most 65536 bytes are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `path` | path | yes | a path of the node that may be read | The file's absolute path. |

### `redis_info`

Show one section of what Redis on a node reports about itself.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["redis-cli", "info", "{section}"]`. Ended after 10 s and killed 5 s later; at most 16384 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `section` | string | no; default `memory` | one of `server`, `clients`, `memory`, `persistence`, `stats`, `replication`, `cpu`, `keyspace` | The section. |

### `redis_ping`

Ask whether Redis on a node answers.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["redis-cli", "ping"]`. Ended after 10 s and killed 5 s later; at most 4096 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `tail_journal`

Show the last journal lines of a service on a node.

- Risk: read. Version of the entry: 1.
- Runs in the node: `["journalctl", "--no-pager", "--unit={unit}", "--lines={lines}", "--since=-{since}"]`. Ended after 20 s and killed 5 s later; at most 65536 bytes of each output stream are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `lines` | integer | no; default `100` | from 1 to 500 | How many lines, counted from the newest. |
| `node` | string | yes | one of the run's nodes | The node. |
| `since` | string | no; default `7d` | matches `^[0-9]{1,4}(s\|m\|h\|d)$` | How far back to look: a number and s, m, h or d, such as 15m. |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\.service$` | The service's unit. |

## Mutating tools

### `apply_config_patch`

Change a configuration file of a node with a unified diff. The file is checked afterwards and put back as it was when the check fails. Only the paths list_nodes names as patchable can be changed.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `config.syntax_error`, `config.wrong_value`, `container.crash_loop`, `database.access_denied`, `database.connections_exhausted`, `network.dns_failure`.
- Carried out by the handler `apply_config_patch`; at most 16384 bytes are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `diff` | text | yes | at most 65536 characters | The change as a unified diff of the file, with exact context lines. |
| `node` | string | yes | one of the run's nodes | The node. |
| `path` | path | yes | a path of the node that may be patched | The file's absolute path. |

### `docker_remove_container`

Remove a container from a node, stopping it first when it runs. It is gone afterwards.

- Risk: high. Version of the entry: 1.
- Fits the diagnoses: `container.crash_loop`, `resource.disk_full`.
- Runs in the node: `["docker", "rm", "--force", "{container}"]`. Ended after 60 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["docker", "ps", "--all"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `container` | string | yes | matches `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` | The container's name or ID, as docker_ps shows it. |
| `node` | string | yes | one of the run's nodes | The node. |

### `docker_restart_container`

Restart a container on a node.

- Risk: low. Version of the entry: 1.
- Fits the diagnoses: `container.crash_loop`, `container.stopped`, `config.syntax_error`, `config.wrong_value`.
- Runs in the node: `["docker", "restart", "{container}"]`. Ended after 90 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["docker", "inspect", "--type=container", {"literal": "--format={{.State.Status}}"}, "{container}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `container` | string | yes | matches `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` | The container's name or ID, as docker_ps shows it. |
| `node` | string | yes | one of the run's nodes | The node. |

### `docker_start_container`

Start a stopped container on a node.

- Risk: low. Version of the entry: 1.
- Fits the diagnoses: `container.stopped`, `container.daemon_stopped`.
- Runs in the node: `["docker", "start", "{container}"]`. Ended after 60 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["docker", "inspect", "--type=container", {"literal": "--format={{.State.Status}}"}, "{container}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `container` | string | yes | matches `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` | The container's name or ID, as docker_ps shows it. |
| `node` | string | yes | one of the run's nodes | The node. |

### `kill_process`

Send a signal to one process of a node.

- Risk: high. Version of the entry: 1.
- Fits the diagnoses: `resource.cpu_saturated`, `resource.memory_exhausted`, `resource.fd_exhausted`, `database.connections_exhausted`.
- Runs in the node: `["kill", "-s", "{signal}", "{pid}"]`. Ended after 15 s and killed 5 s later; at most 4096 bytes of each output stream are given back.
- Checked afterwards by: `["ps", "-o", "pid,stat,etime,args", "--pid={pid}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `pid` | integer | yes | from 2 to 4194304 | The process's ID, as list_processes shows it. |
| `signal` | string | no; default `TERM` | one of `TERM`, `KILL` | TERM asks the process to end; KILL ends it at once. |

### `nginx_reload`

Have nginx on a node load its configuration again without stopping.

- Risk: low. Version of the entry: 1.
- Fits the diagnoses: `config.syntax_error`, `config.wrong_value`.
- Runs in the node: `["systemctl", "reload", "nginx.service"]`. Ended after 30 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["systemctl", "is-active", "nginx.service"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `remove_file`

Delete one file below /tmp, /var/tmp or /var/cache on a node. Directories are not deleted, and a file that is not there is an error.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `resource.disk_full`, `resource.inodes_exhausted`.
- Runs in the node: `["rm", "--", "{path}"]`. Ended after 30 s and killed 5 s later; at most 8192 bytes of each output stream are given back.
- Checked afterwards by: `["df", "--human-readable", "--output=target,pcent,avail", "/tmp", "/var/tmp", "/var/cache"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `path` | path | yes | a path below `/tmp` or `/var/tmp` or `/var/cache` | The file's absolute path. |

### `restart_service`

Restart a systemd service on a node.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `service.stopped`, `service.failed`, `service.disabled`, `config.syntax_error`, `config.wrong_value`, `resource.disk_full`, `resource.inodes_exhausted`, `resource.memory_exhausted`, `resource.fd_exhausted`, `resource.cpu_saturated`, `container.stopped`, `container.daemon_stopped`, `container.crash_loop`, `database.unavailable`, `database.connections_exhausted`, `database.access_denied`, `network.dns_failure`, `permissions.wrong_mode`, `permissions.wrong_owner`.
- Runs in the node: `["systemctl", "restart", "{unit}"]`. Ended after 210 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["systemctl", "is-active", "{unit}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\.service$` | The service's unit. |

### `rotate_logs`

Rotate every log file that the node's logrotate configuration names now, whatever its size or age.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `resource.disk_full`, `resource.inodes_exhausted`.
- Runs in the node: `["logrotate", "--force", "/etc/logrotate.conf"]`. Ended after 300 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["df", "--human-readable", "--output=target,pcent,avail", "/var/log"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |

### `start_service`

Start a systemd service on a node.

- Risk: low. Version of the entry: 1.
- Fits the diagnoses: `service.stopped`, `service.failed`, `service.disabled`, `config.syntax_error`, `config.wrong_value`, `resource.disk_full`, `resource.inodes_exhausted`, `resource.memory_exhausted`, `resource.fd_exhausted`, `container.stopped`, `container.daemon_stopped`, `database.unavailable`, `permissions.wrong_mode`, `permissions.wrong_owner`.
- Runs in the node: `["systemctl", "start", "{unit}"]`. Ended after 120 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["systemctl", "is-active", "{unit}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\.service$` | The service's unit. |

### `stop_service`

Stop a systemd service on a node. This tool does not start it again; a service that a socket or a timer activates can start again by itself.

- Risk: high. Version of the entry: 1.
- Fits the diagnoses: `resource.cpu_saturated`, `resource.memory_exhausted`, `resource.fd_exhausted`, `resource.disk_full`, `container.crash_loop`.
- Runs in the node: `["systemctl", "stop", "{unit}"]`. Ended after 120 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["systemctl", "is-active", "{unit}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes | The node. |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\.service$` | The service's unit. |

### `vacuum_journal`

Shrink a node's journal: rotate it, then delete its oldest archived files until what is left is no larger than a size and holds nothing older than an age. Both bounds apply; leave one at its default to bound by the other alone.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `resource.disk_full`.
- Runs in the node: `["journalctl", "--rotate", "--vacuum-size={size}", "--vacuum-time={age}"]`. Ended after 60 s and killed 5 s later; at most 16384 bytes of each output stream are given back.
- Checked afterwards by: `["journalctl", "--disk-usage"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `age` | string | no; default `10y` | matches `^[0-9]{1,3}(m\|h\|d\|w\|y)$` | The oldest entry to keep: a number and m, h, d, w or y, such as 2d. |
| `node` | string | yes | one of the run's nodes | The node. |
| `size` | string | no; default `4G` | matches `^[0-9]{1,4}[KMG]$` | The most the journal may take: a number and K, M or G, such as 200M. |

## Forbidden tools

A forbidden tool is named so that a call of it is denied by its name. No model is given one, and none is ever carried out.

| Tool | What it would do |
| --- | --- |
| `flush_firewall` | Delete a node's firewall rules. |
| `manage_user` | Add, change or remove a user account or its rights on a node. |
| `reboot` | Restart or shut down a node. |
| `remove_package` | Remove an installed package from a node. |
| `remove_path` | Delete files or directories anywhere on a node. |
