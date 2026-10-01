"""Library bookkeeping for a finished run.

The Library row is what makes a result reproducible months later, so it has to name the
weights that produced the file — not just the source id the user picked. A ``mlx-q4``
request carries no path at all: the staged checkpoint it loads is only known once the run
has resolved it, which is why the row is completed when the job ends rather than when it
starts.
"""

from __future__ import annotations

from refract_backend.library import Library


def _start(library: Library, job_id: str = "j1", model_path: str | None = None) -> None:
    library.record_start(
        job_id=job_id,
        prompt="a prompt",
        negative_prompt=None,
        params={"prompt": "a prompt"},
        references=[],
        seeds=[1],
        model_source="mlx-q4",
        model_path=model_path,
        quantize=None,
    )


def test_finish_records_the_checkpoint_the_run_actually_loaded(tmp_path):
    library = Library(tmp_path / "library.db")
    _start(library)
    assert library.get("j1")["model_path"] is None

    library.record_finish(job_id="j1", status="done", outputs=["/out/a.png"], model_path="/models/staged")

    assert library.get("j1")["model_path"] == "/models/staged"


def test_a_run_that_reports_no_path_does_not_erase_one(tmp_path):
    library = Library(tmp_path / "library.db")
    _start(library, model_path="/models/chosen-by-the-user")

    library.record_finish(job_id="j1", status="done", outputs=[], model_path=None)

    assert library.get("j1")["model_path"] == "/models/chosen-by-the-user"


def test_finish_still_records_the_rest_of_the_result(tmp_path):
    library = Library(tmp_path / "library.db")
    _start(library)

    library.record_finish(
        job_id="j1",
        status="done",
        outputs=["/out/a.png"],
        duration_s=12.5,
        peak_memory_gb=15.6,
        width=384,
        height=672,
        model_path="/models/staged",
    )

    item = library.get("j1")
    assert item["status"] == "done"
    assert item["outputs"] == ["/out/a.png"]
    assert item["duration_s"] == 12.5
    assert item["peak_memory_gb"] == 15.6
    assert (item["width"], item["height"]) == (384, 672)
