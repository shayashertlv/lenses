"""Immutable per-job workflow plans; a saved job keeps the plan it was created with."""

LEGACY_STAGES = ('generate', 'lenses', 'connections', 'texture', 'finish')
STANDARD_STAGES = (*LEGACY_STAGES, 'finish_refine')
PIPELINES = {'standard': STANDARD_STAGES, 'legacy': LEGACY_STAGES}
DEFAULT_PIPELINE = 'standard'
# The plans first shipped as "test" (six stages) and "current" (five stages).
# Saved jobs, receipts and older clients still carry those names.
PIPELINE_ALIASES = {'test': 'standard', 'current': 'legacy'}


def canonical_pipeline(value, default=None):
    """Return the canonical plan name for a saved or requested value."""
    if value is None and default is not None:
        return default
    if isinstance(value, str):
        value = PIPELINE_ALIASES.get(value, value)
        if value in PIPELINES:
            return value
    raise ValueError('Pipeline must be standard or legacy.')


def validate_pipeline(value):
    return canonical_pipeline(value)


def pipeline_name(job):
    # Jobs saved before the plan choice existed follow the original five-call plan.
    return canonical_pipeline(job.get('pipeline'), default='legacy')


def pipeline_stages(job):
    return PIPELINES[pipeline_name(job)]


def repeat_stage(job):
    return pipeline_stages(job)[-1]


def native_stage(stage):
    # The extra post-texture pass inherits the complete material-only lock.
    return 'finish' if stage == 'finish_refine' else stage


def run_disclosure(job):
    if pipeline_name(job) == 'standard':
        return ('Start runs 2 Meshy requests and 4 Astra requests automatically, including '
                '2 material/finish edits after texturing. It stops at the final preview or on failure. '
                'Finish and download the model, or send specific written instructions for 1 more Astra finish request.')
    return ('Start runs 2 Meshy requests and 3 Astra requests automatically. It stops at the final preview or on failure. '
            'Another edit runs 1 Astra finish request.')
