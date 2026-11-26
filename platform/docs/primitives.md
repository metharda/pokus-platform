---
title: Fault primitives and probes
type: reference
audience: [researcher, scenario-author, developer, reader]
applies_to: {platform: ">=0.0"}
last_verified: {date: null, rc: null, by: null}
---

<!-- Written by `make primitives` from the library. Do not edit it by hand. -->

# Fault primitives and probes

The library has 16 fault primitives and 7 probes. A scenario's steps name one and give its parameters: a fault primitive in `setup` and `inject`, a probe in `preconditions`, `confirm`, a ground truth's `verification` and `collateral`, and a blueprint's `health`. Every step acts on one node of the scenario's blueprint, named by `node`.

`potka catalog lint` holds each step's parameters to the step's model, in every instance of the scenario. A table below has a row for each parameter: its type, what must hold of its value (Rule) and what it is (Description). What must hold of several parameters together stands below the table. The lint's finding names the parameter and says what is wrong with it, and shows no value.

A fault primitive refuses a target it cannot be made on before it changes anything there. A probe changes nothing in a target: it passes, fails, or reports an error when it could not measure. A step runs only the programs that its section lists, each as a list of arguments and never through a shell.

## Fault primitives

### `disk.fill`

Fill a node's root disk.

- Version: 1.
- Runs in the node: `findmnt` (util-linux), `fallocate` (util-linux).
- Reports: `size_bytes`, `steps`, `stopped_by`, `target_bytes`, `usage_bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`) | The file the fault makes; its directory is there and on the root disk. |
| `percent` | integer | yes | from 1 to 100 | The share of the root disk's configured size that is in use afterwards. |

### `disk.inode_exhaust`

Use up the inodes of a node's root file system with empty files.

- Version: 1.
- Runs in the node: `findmnt` (util-linux), `stat` (coreutils), `mkdir` (coreutils), `touch` (coreutils).
- Reports: `inodes_total`, `inodes_used`, `stopped_by`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`) | The directory the fault makes for its files; the directory above it is there. |
| `prefix` | string | yes | matches `^[a-z][a-z0-9_.]{0,15}$` | How each file's name begins; a number of seven digits follows. |
| `percent` | integer | yes | from 1 to 100 | The share of the file system's inodes that is in use afterwards. |

### `docker.kill_container`

Kill a running container.

- Version: 1.
- Runs in the node: `systemctl` (systemd), `docker` (docker.io).
- Acts on the service of the package `docker.io`, which the golden image of its node must carry.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `container` | string | yes | matches `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` | The container's name. |

### `docker.stop_daemon`

Stop Docker's daemon and the socket that would start it again.

A unit that requires the daemon and is started later starts it again; that no such unit runs on the node is the scenario's to hold.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Acts on the service of the package `docker.io`, which the golden image of its node must carry.
- Reports: `service_active_state`, `socket_active_state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |

### `file.mutate`

Edit a text file by lines; the file keeps its owner and mode.

- Version: 1.
- Runs no program in the node.
- Reports: `bytes`, `edits`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); no file in a place whose files are run, or name commands that are run (the units of systemd, cron's tables, what a login or a boot reads) | The file, which is there. |
| `edits` | list | yes | 1 to 16 entries | The edits, each made on what the one before it left. |

An entry of `edits` is a mapping; its `edit` says which kind it is:

- `edit`: `set_key`. Give the one line `key = value` of a file another value.
  - `key` (string; matches `^[A-Za-z0-9_.-]{1,128}$`): The key: what stands before the line's first `=`, without blanks.
  - `value` (string; at most 4096 characters; one line of text, without a line's end): What stands after the `=` afterwards.
- `edit`: `replace_line`. Put another line in place of the one line that equals a text.
  - `line` (string; at most 4096 characters; one line of text, without a line's end): The line as it is, without its end; exactly one line of the file equals it.
  - `replacement` (string; at most 4096 characters; one line of text, without a line's end): The line that takes its place.
- `edit`: `insert_after`. Put a new line after the one line that equals a text.
  - `line` (string; at most 4096 characters; one line of text, without a line's end): The line as it is, without its end; exactly one line of the file equals it.
  - `text` (string; at most 4096 characters; one line of text, without a line's end): The new line that follows it.
- `edit`: `delete_line`. Take away the one line that equals a text.
  - `line` (string; at most 4096 characters; one line of text, without a line's end): The line as it is, without its end; exactly one line of the file equals it.

### `file.write`

Write a file, new or in place of what it held.

- Version: 1.
- Runs in the node: `chmod` (coreutils), `chown` (coreutils).
- Reports: `bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); no file in a place whose files are run, or name commands that are run (the units of systemd, cron's tables, what a login or a boot reads) | The file; its directory is there. |
| `content` | string | yes | at most 65536 characters; text that has a UTF-8 form and no NUL | What the file holds afterwards. |
| `mode` | string | no | matches `^[0-7]{3}$`; no execute bit | Its permission bits, none of them an execute bit; a new file's are 644 unless given. |
| `uid` | integer | no | from 0 to 2147483647 | Its owner; a new file's is 0 unless given. |
| `gid` | integer | no | from 0 to 2147483647 | Its group; a new file's is 0 unless given. |

### `net.netem`

Delay or drop what a node sends on its network interface.

The interface is `eth0`.

- Version: 1.
- Runs in the node: `tc` (iproute2).

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `delay_ms` | integer | no | from 1 to 60000 | The milliseconds every packet is held back. |
| `loss_percent` | integer | no | from 1 to 100 | The share of the packets that is dropped. |

One of `delay_ms` and `loss_percent` is given.

### `nginx.corrupt_conf`

Give a configuration file a defect nginx refuses.

- Version: 1.
- Runs no program in the node.
- Acts on the service of the package `nginx`, which the golden image of its node must carry.
- Reports: `bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); a file below `/etc/nginx` | The configuration file. |
| `defect` | string | yes | one of `missing_semicolon`, `unclosed_block`, `unknown_directive`, `bad_upstream_host` | What is done to the file: the first semicolon that a closing brace or the file's end follows is taken away (`missing_semicolon`); the last closing brace is taken away (`unclosed_block`); the first simple directive's name gets its last letter twice (`unknown_directive`); the first proxied address, or else the first upstream server, gets another host (`bad_upstream_host`). |
| `host` | string | no | a host's name of letters, digits, hyphens and dots, not an address | The host that is put in, for `bad_upstream_host` and for no other defect. |

`host` is given with the defect `bad_upstream_host` and with no other.

### `nginx.set_upstream`

Pass a site's requests to another host and port.

- Version: 1.
- Runs no program in the node.
- Acts on the service of the package `nginx`, which the golden image of its node must carry.
- Reports: `bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); a file below `/etc/nginx` | The configuration file. |
| `upstream` | string | yes | a host or an address, a colon and a port | The host or address and the port that requests are passed to afterwards. |

### `permissions.break`

Change the mode or the owner of a file or a directory.

- Version: 1.
- Runs in the node: `stat` (coreutils), `chmod` (coreutils), `chown` (coreutils).

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`) | The file or the directory, which is there. |
| `mode` | string | no | matches `^[0-7]{3}$` | Its permission bits afterwards; no execute bit that it has not. |
| `uid` | integer | no | from 0 to 2147483647 | Its owner afterwards. |
| `gid` | integer | no | from 0 to 2147483647 | Its group afterwards. |

One of `mode`, `uid` and `gid` is given.

### `pg.break_hba`

Make PostgreSQL refuse the connections that come over the network.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Acts on the service of the package `postgresql`, which the golden image of its node must carry.
- Reports: `bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); a cluster's `/etc/postgresql/<version>/<cluster>/pg_hba.conf` | The cluster's `pg_hba.conf`. |
| `unit` | string | yes | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance); no unit of systemd's own or of the lab's channel into a target; PostgreSQL's unit: `postgresql.service`, or one cluster's (a cluster's unit is `postgresql@<version>-<cluster>.service`) | The unit that is asked to read the file again. |
| `defect` | string | yes | one of `reject`, `no_entry` | `reject`: every connection over the network is rejected; `no_entry`: none has a rule. |

The unit is the cluster's whose file it is, or `postgresql.service`.

### `pg.fill_disk`

Fill the root disk from where PostgreSQL keeps its data.

- Version: 1.
- Runs in the node: `findmnt` (util-linux), `fallocate` (util-linux).
- Acts on the service of the package `postgresql`, which the golden image of its node must carry.
- Reports: `size_bytes`, `steps`, `stopped_by`, `target_bytes`, `usage_bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | yes | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); a file below `/var/lib/postgresql` | The file the fault makes, below `/var/lib/postgresql`. |
| `percent` | integer | yes | from 1 to 100 | The share of the root disk's configured size that is in use afterwards. |

### `pg.stop`

Stop PostgreSQL.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Acts on the service of the package `postgresql`, which the golden image of its node must carry.
- Reports: `active_state`, `unit_file_state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `unit` | string | yes | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance); no unit of systemd's own or of the lab's channel into a target; PostgreSQL's unit: `postgresql.service`, or one cluster's (a cluster's unit is `postgresql@<version>-<cluster>.service`) | `postgresql.service` or a cluster's `postgresql@<version>-<cluster>.service`. |

### `redis.stop`

Stop Redis.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Acts on the service of the package `redis-server`, which the golden image of its node must carry.
- Reports: `active_state`, `unit_file_state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `unit` | string | no; default `redis-server.service` | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance); no unit of systemd's own or of the lab's channel into a target; the unit `redis-server.service` and no other | Redis's unit. |

### `systemd.action`

Start, stop, restart, disable or mask a unit.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Reports: `active_state`, `unit_file_state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `unit` | string | yes | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance); no unit of systemd's own or of the lab's channel into a target | The unit. |
| `action` | string | yes | one of `start`, `stop`, `restart`, `disable`, `mask` | What systemd is asked to do with it. |

### `systemd.break_unit`

Give a service a command that cannot be run.

The command replaces the service's own in a drop-in file. Nothing is restarted: the service fails at its next start.

- Version: 1.
- Runs in the node: `systemctl` (systemd), `mkdir` (coreutils).

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `unit` | string | yes | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance); no unit of systemd's own or of the lab's channel into a target; a service | The service. |
| `exec_start` | string | yes | at most 1000 characters; one line of words of letters, digits and `/._=:@,+-`, one blank between them (the first word is a program's absolute path in its normal form, and no word is a shell) | The command line: a program that is not in the target, and its arguments. |

## Probes

### `container_state`

Whether a container of the node's Docker is in a state.

The daemon is asked only while its unit runs: a docker command to a stopped daemon would start it through its socket, and a probe changes nothing. With the daemon stopped no container is said to be `running`, since containers stop with the daemon unless Docker's live restore is set; of any other state nothing can be said then.

- Version: 1.
- Runs in the node: `systemctl` (systemd), `docker` (docker.io).
- Acts on the service of the package `docker.io`, which the golden image of its node must carry.
- Reports: `daemon_active_state`, `state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `container` | string | yes | matches `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` | The container's name. |
| `state` | string | yes | one of `created`, `running`, `paused`, `restarting`, `removing`, `exited`, `dead`, `absent` | One of Docker's words for a container's state, or `absent` for no such container. |

### `disk_usage_below`

Whether a node's root disk is in use below a percentage.

- Version: 1.
- Runs no program in the node.
- Reports: `size_bytes`, `usage_bytes`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `percent` | integer | yes | from 1 to 100 | The share of the root disk's configured size. |
| `below` | boolean | no; default `true` |  | Whether the use must be below that share (true) or at it or above (false). |

### `inode_usage_below`

Whether a file system's inodes are in use below a percentage.

- Version: 1.
- Runs in the node: `stat` (coreutils).
- Reports: `inodes_total`, `inodes_used`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `path` | string | no; default `/` | at most 4096 characters; an absolute path in its normal form (of printable characters without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`); or `/` | A path on the file system that is asked about. |
| `percent` | integer | yes | from 1 to 100 | The share of the file system's inodes. |
| `below` | boolean | no; default `true` |  | Whether the inodes in use must be below that share (true) or at it or above (false). |

### `pg_query`

Whether PostgreSQL on the node answers a statement that reads.

The statement runs as the operating system user `postgres` through the local socket of the cluster on PostgreSQL's default port. Nothing of what it returns is kept or compared: the probe passes when the server ran it, and fails when the server is not reached, refuses the statement or does not end it within five seconds. A statement that the server refuses for what the statement itself is, its syntax or a write, is an error of the probe and no answer.

The session's transactions read only, which stops a statement that would write to the database. It does not stop a function whose effect lies outside the database's tables, such as one that ends another session or has the server read its configuration again: keeping the statement to reading is its author's. Once the session has begun it writes nothing in the server's log; an attempt that the server refuses before that is the server's to log, as for `pg_ready`.

- Version: 1.
- Runs in the node: `id` (coreutils), `psql` (postgresql).
- Acts on the service of the package `postgresql`, which the golden image of its node must carry.
- Reports: `status`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `database` | string | yes | matches `^[a-z_][a-z0-9_]{0,62}$` | The database. |
| `query` | string | yes | at most 1000 characters; one `SELECT` statement on one line, of printable characters, without a semicolon | One `SELECT` statement. |

### `pg_ready`

Whether PostgreSQL on the node accepts connections on its local socket.

The server is asked as the operating system user `postgres`, a role it knows: asked as any other, it would write the attempt in its log at every poll. It still writes a line for an attempt that it refuses before a session begins: while it starts or stops, and when the role `postgres`, the database of that name or the rule for local connections has been taken away. The cluster asked is the one on PostgreSQL's default socket and port.

- Version: 1.
- Runs in the node: `id` (coreutils), `pg_isready` (postgresql).
- Acts on the service of the package `postgresql`, which the golden image of its node must carry.
- Reports: `status`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `ready` | boolean | no; default `true` |  | Whether it must accept connections (true) or must not (false). |

### `redis_ping`

Whether Redis on the node answers on the loopback address.

It is asked on Redis's default port. Only `PONG` is an answer: a server that asks for a password or is still loading its data gives the probe an error, neither a pass nor a fail.

- Version: 1.
- Runs in the node: `redis-cli` (redis-server).
- Acts on the service of the package `redis-server`, which the golden image of its node must carry.
- Reports: `status`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |

### `unit_state`

Whether a unit that systemd has loaded is in a state.

A unit that systemd has not loaded, one it does not know or one that is masked, is in no state that can be asked for: the probe fails for every state, and its facts say what systemd said.

- Version: 1.
- Runs in the node: `systemctl` (systemd).
- Reports: `active_state`, `load_state`.

| Parameter | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` | The alias of the node the step acts on. |
| `unit` | string | yes | a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and `@._:-`; a template's unit has its instance) | The unit, by the form of its name alone: the probe only reads, so the units a fault may not act on, systemd's own and the lab's channel into a target, may be asked about. |
| `state` | string | yes | one of `active`, `reloading`, `inactive`, `failed`, `activating`, `deactivating`, `maintenance` | The state systemd must say the unit is in. |
