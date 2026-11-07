//! The wait queue in front of the requests in flight: a request that finds every place taken waits instead of
//! being refused, in a fair order per client (round robin over the client keys, first come first served within one
//! client), at most `wait` long (then 503 with Retry-After) and at most `cap` of them at once (then 429 with
//! Retry-After). A request that leaves while it waits (its client hung up, or its future was dropped) frees its
//! place at once. A permit is held until the answer's last byte went out (a streamed answer too).
//!
//! The admission check (`admit`, the memory guard of memory.rs) is asked before a request is let in, both at once and
//! from the queue: while it says no, new requests wait in the queue and are not refused; `tick` lets the waiting
//! ones in once it says yes again.

use std::collections::{HashMap, VecDeque};
use std::future::Future;
use std::net::IpAddr;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::oneshot;

/// Whose request it is, for the fair order: the client's own bearer key (a digest of it, never the key), else the
/// account the kernel names for the peer's socket on the board, else the peer's address.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum ClientKey {
    Bearer([u8; 16]),
    Uid(u32),
    Addr(IpAddr),
}

impl ClientKey {
    /// The kind of key, for the log (never its value).
    pub fn kind(&self) -> &'static str {
        match self {
            ClientKey::Bearer(_) => "key",
            ClientKey::Uid(_) => "account",
            ClientKey::Addr(_) => "address",
        }
    }
}

/// Why a request did not get a place.
#[derive(Debug, PartialEq, Eq)]
pub enum Refused {
    /// the queue holds `cap` requests already
    Full,
    /// it waited its whole time
    Timeout,
    /// its client left while it waited
    Gone,
}

/// The upper bounds of the wait-time buckets in seconds (the last one, +Inf, is the count).
pub const BUCKETS: [f64; 8] = [0.1, 0.5, 1.0, 2.0, 5.0, 15.0, 60.0, 120.0];

#[derive(Default)]
pub struct QueueMetrics {
    pub queued_total: AtomicU64,
    pub timeouts: AtomicU64,
    pub full: AtomicU64,
    pub gone: AtomicU64,
    /// times the admission check held a request back
    pub held: AtomicU64,
    pub wait_micros: AtomicU64,
    pub wait_count: AtomicU64,
    pub buckets: [AtomicU64; BUCKETS.len()],
}

impl QueueMetrics {
    fn waited(&self, d: Duration) {
        self.wait_micros.fetch_add(d.as_micros() as u64, Relaxed);
        self.wait_count.fetch_add(1, Relaxed);
        let s = d.as_secs_f64();
        for (i, b) in BUCKETS.iter().enumerate() {
            if s <= *b {
                self.buckets[i].fetch_add(1, Relaxed);
            }
        }
    }
}

struct Waiter {
    id: u64,
    tx: oneshot::Sender<()>,
}

#[derive(Default)]
struct Inner {
    running: usize,
    queued: usize,
    /// the client keys with waiting requests, in turn
    order: VecDeque<ClientKey>,
    lanes: HashMap<ClientKey, VecDeque<Waiter>>,
    next_id: u64,
}

pub type Admit = Box<dyn Fn() -> bool + Send + Sync>;

pub struct Queue {
    max_running: usize,
    cap: usize,
    wait: Duration,
    admit: Admit,
    inner: Mutex<Inner>,
    pub metrics: QueueMetrics,
}

/// One place in flight; given back when dropped.
pub struct Permit {
    queue: Arc<Queue>,
}

impl Drop for Permit {
    fn drop(&mut self) {
        self.queue.release();
    }
}

/// A waiting request's place: dropped before it got its permit (its future was dropped), it leaves the queue, or
/// gives back the permit that was handed to it in the same moment.
struct Waiting {
    queue: Arc<Queue>,
    key: ClientKey,
    id: u64,
    settled: bool,
}

impl Waiting {
    /// Takes the request out of the queue; false when it was let in already (its permit is then in its channel).
    fn leave(&mut self) -> bool {
        self.settled = true;
        let mut inner = self.queue.lock();
        let Some(lane) = inner.lanes.get_mut(&self.key) else { return false };
        let Some(pos) = lane.iter().position(|w| w.id == self.id) else { return false };
        lane.remove(pos);
        if lane.is_empty() {
            inner.lanes.remove(&self.key);
            inner.order.retain(|k| k != &self.key);
        }
        inner.queued -= 1;
        true
    }
}

impl Drop for Waiting {
    fn drop(&mut self) {
        if self.settled {
            return;
        }
        self.queue.metrics.gone.fetch_add(1, Relaxed);
        if !self.leave() {
            // it was let in in the same moment its future was dropped: the place goes on to the next one
            self.queue.release();
        }
    }
}

impl Queue {
    pub fn new(max_running: usize, cap: usize, wait: Duration, admit: Admit) -> Arc<Queue> {
        Arc::new(Queue {
            max_running: max_running.max(1),
            cap,
            wait,
            admit,
            inner: Mutex::new(Inner::default()),
            metrics: QueueMetrics::default(),
        })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn running(&self) -> usize {
        self.lock().running
    }

    pub fn queued(&self) -> usize {
        self.lock().queued
    }

    pub fn cap(&self) -> usize {
        self.cap
    }

    pub fn wait(&self) -> Duration {
        self.wait
    }

    fn release(self: &Arc<Self>) {
        {
            let mut inner = self.lock();
            inner.running = inner.running.saturating_sub(1);
        }
        self.tick();
    }

    /// Lets waiting requests in while places are free and the admission check says yes.
    pub fn tick(&self) {
        {
            let inner = self.lock();
            if inner.queued == 0 || inner.running >= self.max_running {
                return;
            }
        }
        if !(self.admit)() {
            self.metrics.held.fetch_add(1, Relaxed);
            return;
        }
        let mut inner = self.lock();
        while inner.running < self.max_running {
            let Some(key) = inner.order.pop_front() else { break };
            let Some(lane) = inner.lanes.get_mut(&key) else { continue };
            let Some(w) = lane.pop_front() else { continue };
            if lane.is_empty() {
                inner.lanes.remove(&key);
            } else {
                inner.order.push_back(key);
            }
            inner.queued -= 1;
            if w.tx.send(()).is_ok() {
                inner.running += 1;
            }
        }
    }

    /// A place for a request of `key`: at once when one is free and nobody waits, else after its turn in the queue.
    /// `gone` ends when the client left (its connection closed). Ok with the time it waited.
    pub async fn acquire<F>(self: &Arc<Self>, key: ClientKey, gone: F) -> Result<(Permit, Duration), Refused>
    where
        F: Future<Output = ()>,
    {
        let started = Instant::now();
        let free_now = {
            let inner = self.lock();
            inner.queued == 0 && inner.running < self.max_running
        };
        let admitted = free_now && {
            let ok = (self.admit)();
            if !ok {
                self.metrics.held.fetch_add(1, Relaxed);
            }
            ok
        };
        let (mut rx, id) = {
            let mut inner = self.lock();
            if admitted && inner.queued == 0 && inner.running < self.max_running {
                inner.running += 1;
                drop(inner);
                self.metrics.waited(Duration::ZERO);
                return Ok((Permit { queue: self.clone() }, Duration::ZERO));
            }
            if inner.queued >= self.cap {
                drop(inner);
                self.metrics.full.fetch_add(1, Relaxed);
                return Err(Refused::Full);
            }
            let (tx, rx) = oneshot::channel();
            let id = inner.next_id;
            inner.next_id += 1;
            if !inner.lanes.contains_key(&key) {
                inner.order.push_back(key.clone());
            }
            inner.lanes.entry(key.clone()).or_default().push_back(Waiter { id, tx });
            inner.queued += 1;
            (rx, id)
        };
        self.metrics.queued_total.fetch_add(1, Relaxed);
        let mut place = Waiting { queue: self.clone(), key, id, settled: false };
        // a place may be free already (the request found others waiting, or the check said no a moment ago)
        self.tick();
        let outcome = tokio::select! {
            got = &mut rx => if got.is_ok() { None } else { Some(Refused::Gone) },
            _ = tokio::time::sleep(self.wait) => Some(Refused::Timeout),
            _ = gone => Some(Refused::Gone),
        };
        match outcome {
            None => {
                place.settled = true;
            }
            Some(why) => {
                if place.leave() {
                    match why {
                        Refused::Timeout => self.metrics.timeouts.fetch_add(1, Relaxed),
                        _ => self.metrics.gone.fetch_add(1, Relaxed),
                    };
                    return Err(why);
                }
                // let in in the same moment: a timeout takes its place after all, a client that left gives it back
                if why == Refused::Gone {
                    drop(Permit { queue: self.clone() });
                    self.metrics.gone.fetch_add(1, Relaxed);
                    return Err(why);
                }
            }
        }
        let waited = started.elapsed();
        self.metrics.waited(waited);
        Ok((Permit { queue: self.clone() }, waited))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;

    fn never() -> std::future::Pending<()> {
        std::future::pending()
    }

    fn uid(n: u32) -> ClientKey {
        ClientKey::Uid(n)
    }

    #[tokio::test(flavor = "current_thread")]
    async fn waiting_requests_go_in_turn_per_client() {
        let q = Queue::new(1, 16, Duration::from_secs(10), Box::new(|| true));
        let (first, _) = q.acquire(uid(1), never()).await.unwrap();
        let order = Arc::new(Mutex::new(Vec::new()));
        let mut tasks = Vec::new();
        // client 1 queues three requests, then client 2 and client 3 one each
        for (n, who) in [(1, 1), (2, 1), (3, 1), (4, 2), (5, 3)] {
            let (q, order) = (q.clone(), order.clone());
            tasks.push(tokio::spawn(async move {
                let (p, _) = q.acquire(uid(who), never()).await.unwrap();
                order.lock().unwrap().push(n);
                tokio::time::sleep(Duration::from_millis(5)).await;
                drop(p);
            }));
            tokio::task::yield_now().await;
        }
        assert_eq!(q.queued(), 5);
        drop(first);
        for t in tasks {
            t.await.unwrap();
        }
        // round robin over the clients: 1, 2, 3, then the rest of client 1
        assert_eq!(*order.lock().unwrap(), vec![1, 4, 5, 2, 3]);
        assert_eq!((q.running(), q.queued()), (0, 0));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_full_queue_refuses_and_a_long_wait_ends() {
        let q = Queue::new(1, 1, Duration::from_millis(50), Box::new(|| true));
        let (held, _) = q.acquire(uid(1), never()).await.unwrap();
        let q2 = q.clone();
        let waiter = tokio::spawn(async move { q2.acquire(uid(2), never()).await.map(|_| ()) });
        tokio::task::yield_now().await;
        assert_eq!(q.queued(), 1);
        assert_eq!(q.acquire(uid(3), never()).await.err(), Some(Refused::Full));
        assert_eq!(waiter.await.unwrap(), Err(Refused::Timeout));
        assert_eq!(q.queued(), 0);
        assert_eq!(q.metrics.full.load(Relaxed), 1);
        assert_eq!(q.metrics.timeouts.load(Relaxed), 1);
        drop(held);
        assert_eq!(q.running(), 0);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_client_that_leaves_frees_its_place() {
        let q = Queue::new(1, 4, Duration::from_secs(10), Box::new(|| true));
        let (held, _) = q.acquire(uid(1), never()).await.unwrap();
        let (tx, rx) = oneshot::channel::<()>();
        let q2 = q.clone();
        let waiter = tokio::spawn(async move {
            q2.acquire(uid(2), async move {
                let _ = rx.await;
            })
            .await
            .map(|_| ())
        });
        tokio::task::yield_now().await;
        assert_eq!(q.queued(), 1);
        drop(tx);
        assert_eq!(waiter.await.unwrap(), Err(Refused::Gone));
        assert_eq!(q.queued(), 0);
        // a dropped future leaves too
        let q3 = q.clone();
        let dropped = tokio::spawn(async move { q3.acquire(uid(3), never()).await.map(|_| ()) });
        tokio::task::yield_now().await;
        assert_eq!(q.queued(), 1);
        dropped.abort();
        let _ = dropped.await;
        assert_eq!(q.queued(), 0);
        assert_eq!(q.metrics.gone.load(Relaxed), 2);
        drop(held);
        assert_eq!(q.running(), 0);
        assert!(q.acquire(uid(4), never()).await.is_ok());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn while_the_check_says_no_requests_wait_and_go_in_after() {
        let ok = Arc::new(AtomicBool::new(false));
        let flag = ok.clone();
        let q = Queue::new(2, 4, Duration::from_secs(10), Box::new(move || flag.load(Relaxed)));
        let q2 = q.clone();
        let waiter = tokio::spawn(async move { q2.acquire(uid(1), never()).await.map(|_| ()) });
        tokio::task::yield_now().await;
        assert_eq!((q.running(), q.queued()), (0, 1), "a free place, but the check says no: it waits");
        q.tick();
        assert_eq!(q.queued(), 1);
        ok.store(true, Relaxed);
        q.tick();
        assert_eq!(waiter.await.unwrap(), Ok(()));
        assert!(q.metrics.held.load(Relaxed) >= 2);
        assert_eq!((q.running(), q.queued()), (0, 0));
    }
}
