# -*- coding: utf-8 -*-
# Author: Simone Orsi
# Copyright 2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import json
import os
from collections import OrderedDict

from odoo import models, fields, api
from odoo.addons.connector.unit.synchronizer import Importer
from odoo.addons.queue_job.job import (
    DONE, STATES, job)

from .job_mixin import JobRelatedMixin
from ..log import logger
from ..utils.misc import import_klass_from_dotted_path


def get_record_importer(env, importer_dotted_path=None):
    if importer_dotted_path is None:
        return env.get_connector_unit(Importer)
    if not importer_dotted_path.startswith('odoo.addons.'):
        importer_dotted_path = 'odoo.addons.' + importer_dotted_path
    return env.get_connector_unit(
        import_klass_from_dotted_path(importer_dotted_path))


class ImportRecordSet(models.Model, JobRelatedMixin):
    _name = 'import.recordset'
    _inherit = 'import.source.consumer.mixin'
    _description = 'Import recordset'
    _order = 'sequence ASC, create_date DESC'
    _backend_type = 'import_backend'

    backend_id = fields.Many2one(
        'import.backend',
        string='Import Backend'
    )
    sequence = fields.Integer(
        'Sequence',
        help="Sequence for the handle.",
        default=10
    )
    import_type_id = fields.Many2one(
        string='Import type',
        comodel_name='import.type',
        required=True,
    )
    override_existing = fields.Boolean(
        string='Override existing items',
        default=True,
    )
    name = fields.Char(
        string='Name',
        compute='_compute_name',
    )
    create_date = fields.Datetime(
        'Create date',
    )
    record_ids = fields.One2many(
        'import.record',
        'recordset_id',
        string='Records',
    )
    # store info about imports report
    jsondata = fields.Text('JSON Data')
    report_html = fields.Html(
        'Report summary', compute='_compute_report_html')
    full_report_url = fields.Char(
        'Full report url', compute='_compute_full_report_url')
    jobs_global_state = fields.Selection(
        string='Jobs global state',
        selection=STATES,
        compute='_compute_jobs_global_state',
        help=(
            "Tells you if a job is running for this recordset. "
            "If any of the sub jobs is not DONE or FAILED "
            "we assume the global state is PENDING."
        ),
        readonly=True
    )
    report_file = fields.Binary('Report file')
    report_filename = fields.Char('Report filename')
    docs_html = fields.Html(
        'Docs', compute='_compute_docs_html')
    notes = fields.Html('Notes', help="Useful info for your users")

    @api.multi
    def unlink(self):
        # inheritance of non-model mixin - like JobRelatedMixin -
        # does not work w/out this
        return super(ImportRecordSet, self).unlink()

    @api.one
    @api.depends('backend_id.name')
    def _compute_name(self):
        names = [
            self.backend_id.name,
            '#' + str(self.id),
        ]
        self.name = ' '.join(filter(None, names))

    @api.multi
    def set_report(self, values, reset=False):
        """ update import report values
        """
        self.ensure_one()
        if reset:
            _values = {}
        else:
            _values = self.get_report()
        _values.update(values)
        self.jsondata = json.dumps(_values)

    @api.model
    def get_report(self):
        return json.loads(self.jsondata or '{}')

    @api.depends('jsondata')
    def _compute_report_html(self):
        template = self.env.ref('connector_importer.recordset_report')
        for item in self:
            if not item.jsondata:
                continue
            report = item.get_report()
            data = {
                'recordset': item,
                'last_start': report.pop('_last_start'),
                'report_by_model': OrderedDict(),
            }
            # count keys by model
            for _model, __ in item.available_models():
                model = self.env['ir.model'].search(
                    [('model', '=', _model)], limit=1)
                data['report_by_model'][model] = {}
                # be defensive here. At some point
                # we could decide to skip models on demand.
                for k, v in report.get(_model, {}).iteritems():
                    data['report_by_model'][model][k] = len(v)
            item.report_html = template.render(data)

    @api.multi
    def _compute_full_report_url(self):
        for item in self:
            item.full_report_url = \
                '/importer/import-recordset/{}'.format(item.id)

    def debug_mode(self):
        return self.backend_id.debug_mode or \
            os.environ.get('IMPORTER_DEBUG_MODE')

    @api.multi
    @api.depends('job_id.state', 'record_ids.job_id.state')
    def _compute_jobs_global_state(self):
        for item in self:
            item.jobs_global_state = item._get_global_state()

    @api.model
    def _get_global_state(self):
        if not self.job_id:
            return DONE
        res = DONE
        for item in self.record_ids:
            if not item.job_id:
                # TODO: investigate how this is possible
                continue
            # TODO: check why `item.job_state` does not reflect the job state
            if item.job_id.state != DONE:
                res = item.job_id.state
                break
        return res

    def available_models(self):
        return self.import_type_id.available_models()

    @api.multi
    @job
    def import_recordset(self):
        """This job will import a recordset."""
        with self.backend_id.get_environment(self._name) as env:
            importer = env.get_connector_unit(Importer)
            return importer.run(self)

    @api.multi
    def run_import(self):
        """ queue a job for creating records (import.record items)
        """
        job_method = self.with_delay().import_recordset
        if self.debug_mode():
            logger.warn('### DEBUG MODE ACTIVE: WILL NOT USE QUEUE ###')
            job_method = self.import_recordset

        for item in self:
            job = job_method()
            if job:
                # link the job
                item.write({'job_id': job.db_record().id})
            if self.debug_mode():
                # debug mode, no job here: reset it!
                item.write({'job_id': False})
        if self.debug_mode():
            # TODO: port this
            # the "after_all" job needs to be fired manually when in debug mode
            # since the event handler in .events.chunk_finished_subscriber
            # cannot estimate when all the chunks have been processed.
            # for model, importer in self.import_type_id.available_models():
            #     import_record_after_all(
            #         session,
            #         self.backend_id.id,
            #         model,
            #     )
            pass

    @api.multi
    def generate_report(self):
        self.ensure_one()
        reporter = self.get_source().get_reporter()
        if not reporter:
            logger.debug('No reporter found...')
            return
        metadata, content = reporter.report_get(self)
        self.write({
            'report_file': content.encode('base64'),
            'report_filename': metadata['complete_filename']
        })
        logger.info((
            'Report file updated on recordset={}. '
            'Filename: {}'
        ).format(self.id, metadata['complete_filename']))

    def _get_importers(self):
        importers = OrderedDict()

        for _model, importer_dotted_path in self.available_models():
            model = self.env['ir.model'].search(
                [('model', '=', _model)], limit=1)
            with self.backend_id.get_environment(_model) as env:
                importers[model] = get_record_importer(
                    env, importer_dotted_path=importer_dotted_path)
        return importers

    @api.depends('import_type_id')
    def _compute_docs_html(self):
        template = self.env.ref('connector_importer.recordset_docs')
        for item in self:
            if isinstance(item.id, models.NewId):
                continue
            importers = item._get_importers()
            data = {
                'recordset': item,
                'importers': importers,
            }
            item.docs_html = template.render(data)


# TODO
# @job
# def import_record_after_all(
#         session, backend_id, model_name, last_record_id=None, **kw):
#     """This job will import a record."""
#     # TODO: check this
#     model = 'import.record'
#     env = get_environment(session, model, backend_id)
#     # recordset = None
#     # if last_record_id:
#     #     record = env[model].browse(last_record_id)
#     #     recordset = record.recordset_id
#     importer = get_record_importer(env)
#     return importer.after_all()
