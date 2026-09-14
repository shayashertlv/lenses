"""Immutable per-job workflow plans; saved jobs without a choice remain current."""

CURRENT_STAGES = ('generate', 'lenses', 'connections', 'texture', 'finish')
TEST_STAGES = (*CURRENT_STAGES, 'finish_refine')
PIPELINES = {'current': CURRENT_STAGES, 'test': TEST_STAGES}


def validate_pipeline(value):
    if not isinstance(value, str) or value not in PIPELINES:
        raise ValueError('Pipeline must be current or test.')
    return value


def pipeline_name(job):
    return validate_pipeline(job.get('pipeline', 'current'))


def pipeline_stages(job):
    return PIPELINES[pipeline_name(job)]


def repeat_stage(job):
    return pipeline_stages(job)[-1]


def native_stage(stage):
    # The extra post-texture pass inherits the complete material-only lock.
    return 'finish' if stage == 'finish_refine' else stage


def run_disclosure(job):
    if pipeline_name(job) == 'test':
        return ('Start runs 2 Meshy requests and 4 Astra requests automatically, including '
                '2 material/finish edits after texturing. It stops at the final preview or on failure. '
                'Finish and download the model, or send specific written instructions for 1 more Astra finish request.')
    return ('Start runs 2 Meshy requests and 3 Astra requests automatically. It stops at the final preview or on failure. '
            'Another edit runs 1 Astra finish request.')
