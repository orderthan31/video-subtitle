"""Explicit workflow policy, shared by job creation and execution."""
from enum import StrEnum


class WorkflowTemplate(StrEnum):
    EXTRACT_AUDIO = 'extract_audio'
    TRANSCRIBE = 'transcribe'
    TRANSCRIBE_TRANSLATE = 'transcribe_translate'
    TRANSLATE = 'translate'
    ENCODE = 'encode'
    FULL = 'full'


def workflow_plan(template, *, subtitle_input=False, subtitle_mode='burn', audio_input=False):
    template = WorkflowTemplate(template)
    if subtitle_mode not in {'burn', 'soft', 'none'}:
        raise ValueError('Invalid subtitle mode')
    if subtitle_input and template not in {WorkflowTemplate.TRANSLATE, WorkflowTemplate.ENCODE, WorkflowTemplate.FULL}:
        raise ValueError('This workflow does not accept subtitle input')
    if template == WorkflowTemplate.TRANSLATE and not subtitle_input:
        raise ValueError('Translation requires a selected subtitle version')
    if template == WorkflowTemplate.ENCODE and (subtitle_mode != 'none') != bool(subtitle_input):
        raise ValueError('Choose subtitle input or encode without subtitles')
    needs_transcription = template in {WorkflowTemplate.TRANSCRIBE, WorkflowTemplate.TRANSCRIBE_TRANSLATE,
                                      WorkflowTemplate.FULL} and not subtitle_input
    if audio_input and not needs_transcription:
        raise ValueError('Audio reuse requires a transcription workflow')
    stages = ['analyze']
    reused = []
    if template == WorkflowTemplate.EXTRACT_AUDIO or needs_transcription:
        if audio_input:
            reused.append('audio')
        else:
            stages.append('extract_audio')
    if needs_transcription:
        stages.extend(['preprocess_audio', 'transcribe'])
    if subtitle_input:
        reused.append('subtitle')
    if template in {WorkflowTemplate.TRANSLATE, WorkflowTemplate.TRANSCRIBE_TRANSLATE, WorkflowTemplate.FULL}:
        stages.append('translate')
    if needs_transcription or subtitle_input:
        stages.append('generate_subtitle')
    if template in {WorkflowTemplate.FULL, WorkflowTemplate.ENCODE}:
        stages.extend(['encode', 'validate_video'])
    return {'version': 1, 'template': template.value, 'stages': stages, 'reused': reused,
            'paid_stages': [s for s in stages if s in {'transcribe', 'translate'}]}
