"""The inside of an in-memory instance: files, links, the root disk, scripted commands."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from potka_incus.inside import MAX_LINKS, Ended, Execution, Inside, Node, Problem


def tree() -> Inside:
    inside = Inside()
    inside.mkdir("/etc/nginx/sites-available")
    inside.mkdir("/etc/nginx/sites-enabled")
    inside.write("/etc/nginx/sites-available/default", b"server {}\n", uid=0, gid=0, mode=0o644)
    inside.link("/etc/nginx/sites-enabled/default", "../sites-available/default")
    inside.link("/etc/site", "/etc/nginx/sites-enabled/default")
    inside.mkdir("/srv")
    inside.link("/etc/conf", "/etc/nginx")
    return inside


def error(call: Callable[..., object], *arguments: object, **more: object) -> str:
    with pytest.raises(Problem) as raised:
        call(*arguments, **more)
    return raised.value.error


def test_directories_files_and_links_form_a_tree() -> None:
    inside = tree()
    assert inside.listing("/") == ("etc", "srv")
    assert inside.listing("/etc") == ("conf", "nginx", "site")
    assert inside.listing("/etc/nginx") == ("sites-available", "sites-enabled")
    assert inside.nodes["/etc/nginx"] == Node("directory", mode=0o755)
    assert inside.nodes["/etc/site"].kind == "symlink"
    # A directory that is there stays as it is, with its owner, its mode and what it holds.
    inside.mkdir("/etc/nginx", uid=33, mode=0o700)
    assert inside.nodes["/etc/nginx"] == Node("directory", mode=0o755)
    assert inside.listing("/etc/nginx/sites-available") == ("default",)
    inside.mkdir("/var/lib/app", uid=33, gid=34, mode=0o750)
    assert inside.nodes["/var/lib/app"] == Node("directory", b"", 33, 34, 0o750)
    assert inside.nodes["/var/lib"] == Node("directory", mode=0o755)


@pytest.mark.parametrize(
    ("path", "real"),
    [
        ("/", "/"),
        ("/etc/nginx", "/etc/nginx"),
        ("/etc/nginx/sites-enabled/default", "/etc/nginx/sites-available/default"),
        ("/etc/site", "/etc/nginx/sites-available/default"),
        ("/etc/nginx/sites-enabled/../sites-enabled/default", "/etc/nginx/sites-available/default"),
        ("/etc/../etc/nginx/", "/etc/nginx"),
        ("/etc/./nginx/.", "/etc/nginx"),
        ("//etc///nginx", "/etc/nginx"),
        ("/../../etc", "/etc"),
        # A link in the middle of a path is followed like any other.
        ("/etc/conf/sites-available/default", "/etc/nginx/sites-available/default"),
        ("/etc/conf/sites-enabled/default", "/etc/nginx/sites-available/default"),
        ("/etc/conf/..", "/etc"),
    ],
)
def test_a_real_path_follows_every_link(path: str, real: str) -> None:
    assert tree().realpath(path) == real


def test_a_path_that_leads_nowhere_through_a_file_or_in_a_circle_is_an_error() -> None:
    inside = tree()
    inside.link("/etc/a", "/etc/b")
    inside.link("/etc/b", "a")
    inside.link("/etc/dangling", "/etc/nowhere")
    assert error(inside.realpath, "/etc/nginx/missing") == "ENOENT"
    assert error(inside.realpath, "/etc/missing/file") == "ENOENT"
    assert error(inside.realpath, "/etc/dangling") == "ENOENT"
    assert error(inside.realpath, "/etc/a") == "ELOOP"
    assert error(inside.realpath, "/etc/a/x") == "ELOOP"
    # A file is no directory: nothing is below it, and it has no parent to go back to.
    assert error(inside.realpath, "/etc/site/..") == "ENOTDIR"
    assert error(inside.realpath, "/etc/nginx/sites-available/default/x") == "ENOTDIR"
    assert error(inside.realpath, "/etc/nginx/sites-available/default/") == "ENOTDIR"
    assert error(inside.realpath, "/etc/site/.") == "ENOTDIR"
    for relative in ("", "etc", "./etc"):
        with pytest.raises(ValueError, match="not an absolute path"):
            inside.realpath(relative)


def test_as_many_links_as_linux_follows_and_not_one_more() -> None:
    inside = Inside()
    inside.write("/file", b"x")
    inside.link("/1", "/file")
    for number in range(2, MAX_LINKS + 2):
        inside.link(f"/{number}", f"/{number - 1}")
    assert inside.realpath(f"/{MAX_LINKS}") == "/file"
    assert error(inside.realpath, f"/{MAX_LINKS + 1}") == "ELOOP"


def test_a_link_itself_is_what_a_lookup_of_its_path_gives() -> None:
    inside = tree()
    assert inside.lookup("/etc/site") == ("/etc/site", inside.nodes["/etc/site"])
    # Only the last part is left alone: a link before it is followed.
    assert inside.lookup("/etc/conf/sites-enabled/default") == (
        "/etc/nginx/sites-enabled/default",
        inside.nodes["/etc/nginx/sites-enabled/default"],
    )
    assert inside.lookup("/etc/conf/")[0] == "/etc/nginx"
    assert error(inside.lookup, "/etc/missing") == "ENOENT"


def test_a_write_makes_a_file_or_changes_one_and_keeps_its_owner() -> None:
    inside = tree()
    inside.write("/etc/new", b"one", uid=33, gid=34, mode=0o600)
    assert inside.nodes["/etc/new"] == Node("file", b"one", 33, 34, 0o600)
    inside.write("/etc/new", b"two", uid=0, gid=0, mode=0o644, append=True)
    assert inside.nodes["/etc/new"] == Node("file", b"onetwo", 33, 34, 0o600)
    inside.write("/etc/new", b"x", uid=1, gid=1, mode=0o777)
    assert inside.nodes["/etc/new"] == Node("file", b"x", 33, 34, 0o600)
    # An append needs its file: nothing is made for it.
    assert error(inside.write, "/etc/other", b"x", append=True) == "ENOENT"
    assert "/etc/other" not in inside.nodes
    assert error(inside.write, "/etc/missing/file", b"x") == "ENOENT"
    assert error(inside.write, "/etc/new/file", b"x") == "ENOTDIR"
    assert error(inside.write, "/etc/nginx", b"x") == "EISDIR"
    assert error(inside.write, "/etc/new/", b"x") == "ENOTDIR"
    assert error(inside.write, "/etc/fresh/", b"x") == "EISDIR"
    assert "/etc/fresh" not in inside.nodes


def test_a_write_goes_through_links_wherever_they_are_in_its_path() -> None:
    inside = tree()
    inside.write("/etc/site", b"through two links")
    assert inside.nodes["/etc/nginx/sites-available/default"].content == b"through two links"
    assert inside.nodes["/etc/site"].kind == "symlink"
    # Through a link to a directory: the file is made where the link leads.
    inside.write("/etc/conf/nginx.conf", b"events {}\n", uid=33)
    assert inside.nodes["/etc/nginx/nginx.conf"] == Node("file", b"events {}\n", 33, 0, 0o644)
    assert "/etc/conf/nginx.conf" not in inside.nodes
    # Through a link whose target is not there yet: the target is made, as Linux makes it.
    inside.link("/srv/later", "/etc/nginx/later.conf")
    inside.write("/srv/later", b"x")
    assert inside.nodes["/etc/nginx/later.conf"].content == b"x"
    inside.link("/srv/nowhere", "/missing/file")
    assert error(inside.write, "/srv/nowhere", b"x") == "ENOENT"
    inside.link("/srv/a", "/srv/b")
    inside.link("/srv/b", "/srv/a")
    assert error(inside.write, "/srv/a", b"x") == "ELOOP"
    inside.link("/srv/dir", "/etc")
    assert error(inside.write, "/srv/dir", b"x") == "EISDIR"


def test_a_link_and_a_directory_are_made_only_where_nothing_is() -> None:
    inside = tree()
    assert error(inside.link, "/etc/site", "/etc") == "EEXIST"
    assert error(inside.link, "/etc/nginx", "/srv") == "EEXIST"
    assert error(inside.link, "/nowhere/link", "/etc") == "ENOENT"
    inside.link("/etc/conf/link", "x")
    assert inside.nodes["/etc/nginx/link"] == Node("symlink", b"x", mode=0o777)
    assert inside.listing("/etc/nginx") == ("link", "sites-available", "sites-enabled")
    # A directory over a file or below one is refused; through a link it is made behind it.
    assert error(inside.mkdir, "/etc/nginx/sites-available/default") == "EEXIST"
    assert error(inside.mkdir, "/etc/nginx/sites-available/default/sub") == "ENOTDIR"
    inside.mkdir("/etc/conf/conf.d")
    assert inside.nodes["/etc/nginx/conf.d"].kind == "directory"
    with pytest.raises(ValueError, match="not an absolute path"):
        inside.mkdir("etc/x")


def test_a_delete_takes_a_file_a_link_itself_or_an_empty_directory() -> None:
    inside = tree()
    assert error(inside.delete, "/etc/nginx/sites-available") == "ENOTEMPTY"
    assert error(inside.delete, "/etc/missing") == "ENOENT"
    assert error(inside.delete, "/") == "EBUSY"
    assert error(inside.delete, "/etc/site/x") == "ENOTDIR"
    # A link to a directory goes; the directory and what it holds stay.
    inside.delete("/etc/conf")
    assert inside.listing("/etc/nginx") == ("sites-available", "sites-enabled")
    inside.delete("/etc/site")
    inside.link("/etc/conf", "/etc/nginx")
    inside.delete("/etc/conf/sites-available/default")
    assert "/etc/nginx/sites-available/default" not in inside.nodes
    inside.delete("/etc/nginx/sites-available")
    assert inside.listing("/etc/nginx") == ("sites-enabled",)
    # The link that led to the file stays, now leading nowhere.
    assert inside.nodes["/etc/nginx/sites-enabled/default"].kind == "symlink"
    assert error(inside.realpath, "/etc/nginx/sites-enabled/default") == "ENOENT"


def test_a_delete_never_follows_a_last_link_whatever_ends_the_path() -> None:
    inside = Inside()
    inside.mkdir("/d")
    inside.link("/l", "/d")
    inside.write("/f", b"")
    # With a final slash a link or a file is no directory to remove; "." is none either.
    assert error(inside.delete, "/l/") == "ENOTDIR"
    assert error(inside.delete, "/f/") == "ENOTDIR"
    assert error(inside.delete, "/d/.") == "EINVAL"
    assert error(inside.delete, "/l/.") == "EINVAL"
    assert error(inside.delete, "/missing/") == "ENOENT"
    assert sorted(inside.nodes) == ["/", "/d", "/f", "/l"]
    inside.delete("/d/")
    inside.delete("/l")
    assert sorted(inside.nodes) == ["/", "/f"]


def test_nothing_is_made_behind_a_link_to_nothing_or_after_a_missing_part() -> None:
    inside = Inside()
    inside.link("/l", "/t")
    # mkdir never makes a link's target: the name is taken.
    assert error(inside.mkdir, "/l") == "EEXIST"
    assert error(inside.mkdir, "/l/sub") == "EEXIST"
    # A final "." is a step of its own: the part before it must be there.
    assert error(inside.write, "/x/.", b"") == "ENOENT"
    assert error(inside.allocate, "/x/.", 1) == "ENOENT"
    assert error(inside.link, "/x/.", "t") == "ENOENT"
    assert error(inside.link, "/x/", "t") == "ENOENT"
    assert error(inside.link, "/e", "") == "ENOENT"
    assert sorted(inside.nodes) == ["/", "/l"]


def test_a_link_s_target_with_a_final_slash_names_a_directory() -> None:
    inside = Inside()
    inside.write("/f", b"")
    inside.mkdir("/d")
    inside.link("/to-file", "/f/")
    inside.link("/to-directory", "/d/")
    inside.link("/to-nothing", "/new/")
    assert error(inside.realpath, "/to-file") == "ENOTDIR"
    assert inside.realpath("/to-directory") == "/d"
    assert error(inside.realpath, "/to-nothing") == "ENOENT"
    assert error(inside.write, "/to-nothing", b"x") == "EISDIR"
    assert "/new" not in inside.nodes
    # A link without a target (which link() does not make) leads nowhere.
    inside.nodes["/e"] = Node("symlink", b"", mode=0o777)
    assert error(inside.realpath, "/e/d") == "ENOENT"
    assert error(inside.realpath, "/e") == "ENOENT"


def test_only_a_directory_is_listed() -> None:
    inside = tree()
    assert inside.listing("/etc/conf") == ("sites-available", "sites-enabled")
    assert inside.listing("/etc/conf/") == ("sites-available", "sites-enabled")
    assert error(inside.listing, "/etc/site") == "ENOTDIR"
    assert error(inside.listing, "/etc/missing") == "ENOENT"
    # A name that begins like another's is not below it.
    inside.mkdir("/etc/nginx-extra")
    inside.write("/etc/nginx-extra/file", b"")
    assert inside.listing("/etc/nginx") == ("sites-available", "sites-enabled")


@pytest.mark.parametrize(("driver", "full"), [("lvm", "ENOSPC"), ("btrfs", "EDQUOT")])
def test_the_root_disk_counts_files_and_allocations_and_fills_up(driver: str, full: str) -> None:
    inside = Inside(size=1000, driver=driver, base=400)
    inside.mkdir("/data")
    inside.write("/data/a", b"x" * 100)
    inside.link("/data/link", "a" * 300)
    inside.link("/d", "/data")
    assert inside.usage == 500
    assert inside.allocate("/d/fill", 450) is None
    assert (inside.usage, inside.nodes["/data/fill"]) == (950, Node("file", allocated=450))
    # The same length again takes nothing: those bytes are the file's already.
    assert inside.allocate("/data/fill", 450) is None
    assert inside.usage == 950
    # An allocation that does not fit takes nothing; the file made for it stays, empty.
    assert inside.allocate("/data/more", 51) == full
    assert (inside.usage, inside.nodes["/data/more"]) == (950, Node("file"))
    assert inside.allocate("/data/fill", 500) is None
    assert inside.usage == 1000
    assert inside.allocate("/data/fill", 501) == full
    assert inside.nodes["/data/fill"].allocated == 500
    # What is written into an allocation takes nothing more; what goes beyond it does.
    inside.write("/data/fill", b"y" * 500, append=True)
    assert inside.usage == 1000
    assert error(inside.write, "/data/fill", b"y", append=True) == full
    assert inside.allocate("/data/a", 100) is None
    assert inside.usage == 1000
    # Deleting the file gives its bytes back.
    inside.delete("/data/fill")
    assert inside.usage == 500
    for amount in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="not a number of bytes to allocate"):
            inside.allocate("/data/fill", amount)  # type: ignore[arg-type]
    assert error(inside.allocate, "/data", 1) == "EISDIR"
    assert error(inside.allocate, "/data/new/", 1) == "EISDIR"
    assert error(inside.allocate, "/missing/fill", 1) == "ENOENT"
    assert inside.listing("/data") == ("a", "link", "more")


@pytest.mark.parametrize(("driver", "full"), [("lvm", "ENOSPC"), ("btrfs", "EDQUOT")])
def test_a_write_to_a_full_disk_keeps_what_fits_and_fails(driver: str, full: str) -> None:
    inside = Inside(size=1000, driver=driver, base=900)
    inside.write("/a", b"x" * 60)
    # A new file: made, with the bytes that fitted, as the server made it (no owner given).
    assert error(inside.write, "/b", b"y" * 51, uid=33, mode=0o600) == full
    assert inside.nodes["/b"] == Node("file", b"y" * 40)
    assert inside.usage == 1000
    # An append: what was there stays, and nothing more fits.
    assert error(inside.write, "/a", b"z", append=True) == full
    assert inside.nodes["/a"].content == b"x" * 60
    # A replacement empties the file first, so its own bytes are free again: 60 fit, 61 do not.
    inside.write("/a", b"n" * 60)
    assert error(inside.write, "/a", b"m" * 61) == full
    assert inside.nodes["/a"].content == b"m" * 60
    # Through a link, the same.
    inside.link("/l", "/a")
    assert error(inside.write, "/l", b"k" * 100) == full
    assert inside.nodes["/a"].content == b"k" * 60
    inside.delete("/b")
    assert inside.usage == 960
    # A replacement gives back what was allocated for the file too.
    assert inside.allocate("/a", 100) is None
    assert inside.usage == 1000
    inside.write("/a", b"")
    assert (inside.usage, inside.nodes["/a"].allocated) == (900, 0)


def test_a_root_disk_without_a_size_never_fills_up() -> None:
    inside = Inside()
    assert (inside.size, inside.usage) == (None, 0)
    assert inside.allocate("/fill", 10**15) is None
    inside.write("/big", b"x" * 1000)
    assert inside.usage == 10**15 + 1000


def test_the_driver_decides_the_error_and_the_usual_file_system() -> None:
    assert (Inside().driver, Inside().filesystem) == ("lvm", "ext4")
    assert Inside(driver="btrfs").filesystem == "btrfs"
    # A file system of the test's choice on either driver.
    assert Inside(driver="lvm", filesystem="btrfs").filesystem == "btrfs"
    for driver in ("ext4", "zfs", "", "LVM"):
        with pytest.raises(ValueError, match="not a storage pool driver of the stand-in"):
            Inside(driver=driver)


def test_two_insides_are_each_their_own() -> None:
    one, two = Inside(), Inside()
    one.write("/a", b"x")
    one.on(["true"])
    assert two.nodes == {"/": Node("directory", mode=0o755)}
    with pytest.raises(AssertionError):
        two.run(Execution(("true",)))
    # What a test scripted is no part of what two insides are compared by.
    other = Inside()
    other.write("/a", b"x")
    assert one == other
    assert "_answers" not in repr(one)


def test_a_command_ends_as_scripted_for_exactly_its_arguments() -> None:
    inside = Inside()
    inside.on(["systemctl", "is-active", "nginx.service"], 3, b"inactive\n")
    inside.on(("true",))
    asked = Execution(("systemctl", "is-active", "nginx.service"), (("LANG", "C"),), "/", 33, 34)
    assert inside.run(asked) == Ended(3, b"inactive\n", b"")
    assert inside.run(Execution(("true",))) == Ended()
    assert inside.executions == [asked, Execution(("true",))]
    unscripted = [("systemctl", "is-active"), ("systemctl", "is-active", "nginx.service", "x"), ()]
    for argv in unscripted:
        with pytest.raises(AssertionError, match="no answer scripted for the command"):
            inside.run(Execution(argv, (("A", "b"),), "/srv", 1, 2))
    # A command nobody scripted is kept too, with all it was asked with.
    assert inside.executions[2:] == [
        Execution(argv, (("A", "b"),), "/srv", 1, 2) for argv in unscripted
    ]
    for wrong in ("true", b"true", ["true", 7], (a for a in ["true"]), {"true": 1}):
        with pytest.raises(TypeError, match="a scripted command is a list of strings"):
            inside.on(wrong)  # type: ignore[arg-type]


def test_a_function_ends_the_commands_it_knows_and_the_last_script_wins() -> None:
    inside = Inside(size=100)
    inside.mkdir("/data")

    def fallocate(target: Inside, asked: Execution) -> Ended | None:
        if asked.argv[:2] != ("fallocate", "-l"):
            return None
        full = target.allocate(asked.argv[3], int(asked.argv[2]))
        return Ended(1, b"", full.encode()) if full else Ended()

    inside.answer(fallocate)
    assert inside.run(Execution(("fallocate", "-l", "60", "/data/f"))) == Ended()
    # Run again, the command finds its bytes allocated and takes no more.
    assert inside.run(Execution(("fallocate", "-l", "60", "/data/f"))) == Ended()
    assert inside.run(Execution(("fallocate", "-l", "101", "/data/f"))) == Ended(1, b"", b"ENOSPC")
    assert (inside.usage, inside.listing("/data")) == (60, ("f",))
    inside.on(("fallocate", "-l", "1", "/data/f"), 9)
    assert inside.run(Execution(("fallocate", "-l", "1", "/data/f"))) == Ended(9)
    assert inside.usage == 60
    # Of two scripts for one command, the later one answers.
    inside.on(("fallocate", "-l", "1", "/data/f"), 7)
    assert inside.run(Execution(("fallocate", "-l", "1", "/data/f"))) == Ended(7)
    with pytest.raises(AssertionError):
        inside.run(Execution(("rm", "/data/f")))
