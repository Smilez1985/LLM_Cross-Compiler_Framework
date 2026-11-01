//! The few calls into the C library (musl in the static build) that the switch needs, each wrapped once with its
//! error as `io::Error`. This is the only module with unsafe code: every block is one call with plain values or
//! pointers to memory this function owns for the whole call, and the comment above it says why it is sound.
#![allow(unsafe_code)]

use std::io;

fn check(rc: libc::c_int) -> io::Result<()> {
    if rc == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// setgroups(2): exactly these supplementary groups (empty: none).
pub fn setgroups(groups: &[u32]) -> io::Result<()> {
    let list: Vec<libc::gid_t> = groups.to_vec();
    // SAFETY: the pointer and the length describe `list`, which lives until the call returns; an empty list passes
    // a valid (dangling but never read) pointer with length 0, which the kernel accepts as "no groups".
    check(unsafe { libc::setgroups(list.len() as libc::size_t, list.as_ptr()) })
}

/// getgroups(2): the process's supplementary groups.
pub fn getgroups() -> io::Result<Vec<u32>> {
    // SAFETY: a count query (size 0, null pointer) writes nothing.
    let count = unsafe { libc::getgroups(0, std::ptr::null_mut()) };
    if count < 0 {
        return Err(io::Error::last_os_error());
    }
    let mut list: Vec<libc::gid_t> = vec![0; count as usize];
    // SAFETY: `list` has room for exactly `count` entries and lives until the call returns.
    let got = unsafe { libc::getgroups(count, list.as_mut_ptr()) };
    if got < 0 {
        return Err(io::Error::last_os_error());
    }
    list.truncate(got as usize);
    Ok(list)
}

/// setresgid(2) with the same id for the real, effective and saved group id.
pub fn setresgid(gid: u32) -> io::Result<()> {
    // SAFETY: plain integer arguments.
    check(unsafe { libc::setresgid(gid, gid, gid) })
}

/// setresuid(2) with the same id for the real, effective and saved user id.
pub fn setresuid(uid: u32) -> io::Result<()> {
    // SAFETY: plain integer arguments.
    check(unsafe { libc::setresuid(uid, uid, uid) })
}

/// getresuid(2): (real, effective, saved).
pub fn getresuid() -> io::Result<(u32, u32, u32)> {
    let (mut r, mut e, mut s): (libc::uid_t, libc::uid_t, libc::uid_t) = (0, 0, 0);
    // SAFETY: three pointers to local variables that outlive the call.
    check(unsafe { libc::getresuid(&mut r, &mut e, &mut s) })?;
    Ok((r, e, s))
}

/// getresgid(2): (real, effective, saved).
pub fn getresgid() -> io::Result<(u32, u32, u32)> {
    let (mut r, mut e, mut s): (libc::gid_t, libc::gid_t, libc::gid_t) = (0, 0, 0);
    // SAFETY: three pointers to local variables that outlive the call.
    check(unsafe { libc::getresgid(&mut r, &mut e, &mut s) })?;
    Ok((r, e, s))
}

/// prctl(2) with integer arguments only; its result (which may be a value, not only 0).
fn prctl(option: libc::c_int, a: libc::c_ulong, b: libc::c_ulong) -> io::Result<libc::c_int> {
    let zero: libc::c_ulong = 0;
    // SAFETY: prctl is variadic; every option used here takes integer arguments only, passed as unsigned long as
    // the kernel reads them (no pointer is ever passed).
    let rc = unsafe { libc::prctl(option, a, b, zero, zero) };
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(rc)
    }
}

/// PR_SET_KEEPCAPS: keep the permitted capabilities across the switch away from uid 0 (cleared again by execve).
pub fn set_keepcaps() -> io::Result<()> {
    prctl(libc::PR_SET_KEEPCAPS, 1, 0).map(|_| ())
}

/// PR_SET_NO_NEW_PRIVS.
pub fn set_no_new_privs() -> io::Result<()> {
    prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0).map(|_| ())
}

/// PR_GET_NO_NEW_PRIVS.
pub fn no_new_privs() -> io::Result<bool> {
    prctl(libc::PR_GET_NO_NEW_PRIVS, 0, 0).map(|v| v == 1)
}

/// PR_CAPBSET_READ: whether capability `cap` is in the bounding set.
pub fn bounding_has(cap: u32) -> io::Result<bool> {
    prctl(libc::PR_CAPBSET_READ, cap as libc::c_ulong, 0).map(|v| v == 1)
}

/// PR_CAPBSET_DROP: capability `cap` out of the bounding set (needs CAP_SETPCAP in the effective set).
pub fn bounding_drop(cap: u32) -> io::Result<()> {
    prctl(libc::PR_CAPBSET_DROP, cap as libc::c_ulong, 0).map(|_| ())
}

/// PR_CAP_AMBIENT_IS_SET.
pub fn ambient_has(cap: u32) -> io::Result<bool> {
    prctl(
        libc::PR_CAP_AMBIENT,
        libc::PR_CAP_AMBIENT_IS_SET as libc::c_ulong,
        cap as libc::c_ulong,
    )
    .map(|v| v == 1)
}

/// PR_CAP_AMBIENT_RAISE (the capability must be permitted and inheritable).
pub fn ambient_raise(cap: u32) -> io::Result<()> {
    prctl(
        libc::PR_CAP_AMBIENT,
        libc::PR_CAP_AMBIENT_RAISE as libc::c_ulong,
        cap as libc::c_ulong,
    )
    .map(|_| ())
}

/// PR_CAP_AMBIENT_LOWER.
pub fn ambient_lower(cap: u32) -> io::Result<()> {
    prctl(
        libc::PR_CAP_AMBIENT,
        libc::PR_CAP_AMBIENT_LOWER as libc::c_ulong,
        cap as libc::c_ulong,
    )
    .map(|_| ())
}

/// PR_CAP_AMBIENT_CLEAR_ALL.
pub fn ambient_clear() -> io::Result<()> {
    prctl(
        libc::PR_CAP_AMBIENT,
        libc::PR_CAP_AMBIENT_CLEAR_ALL as libc::c_ulong,
        0,
    )
    .map(|_| ())
}

/// The process's capability sets (64-bit masks), as capget(2) version 3 gives them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CapSets {
    pub effective: u64,
    pub permitted: u64,
    pub inheritable: u64,
}

const CAP_VERSION_3: u32 = 0x2008_0522;

#[repr(C)]
struct CapHeader {
    version: u32,
    pid: libc::c_int,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct CapData {
    effective: u32,
    permitted: u32,
    inheritable: u32,
}

/// capget(2) of this process.
pub fn capget() -> io::Result<CapSets> {
    let mut header = CapHeader {
        version: CAP_VERSION_3,
        pid: 0,
    };
    let mut data = [CapData::default(); 2];
    // SAFETY: version 3 reads into exactly two CapData entries (repr(C), the kernel's layout); both pointers point
    // to locals that outlive the call. Capabilities are per thread; this program never starts another thread.
    let rc = unsafe {
        libc::syscall(
            libc::SYS_capget,
            &mut header as *mut CapHeader,
            data.as_mut_ptr(),
        )
    };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    let join = |lo: u32, hi: u32| (u64::from(hi) << 32) | u64::from(lo);
    Ok(CapSets {
        effective: join(data[0].effective, data[1].effective),
        permitted: join(data[0].permitted, data[1].permitted),
        inheritable: join(data[0].inheritable, data[1].inheritable),
    })
}

/// capset(2) of this process.
pub fn capset(sets: CapSets) -> io::Result<()> {
    let mut header = CapHeader {
        version: CAP_VERSION_3,
        pid: 0,
    };
    let split = |v: u64| (v as u32, (v >> 32) as u32);
    let (e0, e1) = split(sets.effective);
    let (p0, p1) = split(sets.permitted);
    let (i0, i1) = split(sets.inheritable);
    let data = [
        CapData {
            effective: e0,
            permitted: p0,
            inheritable: i0,
        },
        CapData {
            effective: e1,
            permitted: p1,
            inheritable: i1,
        },
    ];
    // SAFETY: version 3 reads exactly two CapData entries (repr(C)); both pointers point to locals that outlive the
    // call. The program is single-threaded, so the calling thread is the whole process.
    let rc = unsafe {
        libc::syscall(
            libc::SYS_capset,
            &mut header as *mut CapHeader,
            data.as_ptr(),
        )
    };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}
