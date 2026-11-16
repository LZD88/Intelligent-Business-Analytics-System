from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
import time

import pytest

from chatbi.application.progress import checkpoint, QueryStopped
from chatbi.application.query_jobs import QueryJobs
from chatbi.infrastructure.query_history import QueryHistory
from chatbi.infrastructure.shared_queue import LEASE, SharedQueue
from chatbi.domain.complex_analysis import StepExecutionResult, StepStatus
from tests.test_complex_agents import plan, step
from tests.test_failure_control import executor, VALID_SQL
from tests.test_query_jobs import wait_until


def test_two_process_style_runners_claim_once_and_cancel_shared_job(tmp_path):
    history = QueryHistory(tmp_path / "history.db")
    started, release = Event(), Event()
    calls = []
    lock = Lock()
    def run(question, query_id):
        with lock:
            calls.append(query_id)
        started.set()
        while not release.wait(.01):
            checkpoint()
        return {"question": question}
    one = QueryJobs(history,run,shared=True,workers=2,lease_seconds=5)
    two = QueryJobs(QueryHistory(history.path),run,shared=True,workers=2,lease_seconds=5)
    try:
        identity = one.submit("一项任务")
        assert started.wait(2)
        assert two.cancel(identity)
        wait_until(lambda: history.get(identity)["status"] == "cancelled")
        assert calls == [identity]
    finally:
        release.set()
        one.close();two.close()


def test_expired_lease_interrupts_and_fences_stale_history_writes(tmp_path):
    history = QueryHistory(tmp_path / "history.db")
    queue = SharedQueue(history,8,240,5)
    history.create("old","原任务")
    with history.connect() as db:
        db.execute("INSERT INTO shared_jobs VALUES(?,?,?, ?,?,0)",("old","原任务",time.time()+60,"stale",time.time()-1))
    token = LEASE.set(("old","stale"))
    try:
        with pytest.raises(QueryStopped) as stopped:
            history.finish("old","succeeded",response={"invented":True})
        assert stopped.value.code == "lease_lost"
    finally:
        LEASE.reset(token)
    try:
        from queue import Empty
        with pytest.raises(Empty):
            queue.get(timeout=.05)
        assert history.get("old")["status"] == "interrupted"
        assert history.get("old")["response"] is None
    finally:
        queue.close()


def test_independent_dag_branches_overlap_and_dependent_waits():
    agent = executor([VALID_SQL])
    agent.settings = agent.settings.model_copy(update={"complex_parallel_workers":2})
    gate = Barrier(2)
    finished=[]
    def run(question,p,s,*args):
        if s.step_id in {"step_1","step_2"}:
            gate.wait(timeout=2)
        else:
            assert set(finished)=={"step_1","step_2"}
        finished.append(s.step_id)
        return StepExecutionResult(step_id=s.step_id,step_name=s.step_name,status=StepStatus.SUCCEEDED,
            result="完成",result_reference=s.step_id)
    agent._run_step=run
    result=agent.execute("分析",plan(step(),step("step_2"),step("step_3",depends_on=["step_1","step_2"])),"schema","指标","id")
    assert [s.step_id for s in result]==["step_1","step_2","step_3"]
    assert all(s.status is StepStatus.SUCCEEDED for s in result)


def test_parallel_core_failure_cancels_branches_and_blocks_new_schedule():
    agent = executor([VALID_SQL])
    agent.settings = agent.settings.model_copy(update={"complex_parallel_workers":2})
    gate=Barrier(2)
    calls=[]
    def run(question,p,s,*args):
        calls.append(s.step_id)
        gate.wait(timeout=2)
        if s.step_id=="step_1":
            return StepExecutionResult(step_id=s.step_id,step_name=s.step_name,status=StepStatus.FAILED,
                importance="high",result="失败",result_reference=s.step_id)
        while True:
            time.sleep(.01)
            checkpoint()
    agent._run_step=run
    result=agent.execute("分析",plan(step().model_copy(update={"importance":"high"}),step("step_2"),step("step_3",depends_on=["step_2"])),"schema","指标","id")
    assert result[0].recovery_action=="suspend"
    assert result[1].status is StepStatus.BLOCKED
    assert result[2].status is StepStatus.BLOCKED
    assert set(calls)=={"step_1","step_2"}
