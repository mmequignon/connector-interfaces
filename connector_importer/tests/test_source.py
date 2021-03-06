# Author: Simone Orsi
# Copyright 2018 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).


from .common import FakeModelTestCase
from .fake_models import (
    FakeSourceStatic,
    FakeSourceConsumer,
)
import mock

MOD_PATH = 'odoo.addons.connector_importer.models'
SOURCE_MODEL = MOD_PATH + '.sources.source_mixin.ImportSourceConsumerMixin'


class TestSource(FakeModelTestCase):

    TEST_MODELS_KLASSES = [FakeSourceStatic, FakeSourceConsumer]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_models()
        cls.source = cls._create_source()
        cls.consumer = cls._create_consumer()

    @classmethod
    def tearDownClass(cls):
        cls._teardown_models()
        super().tearDownClass()

    @classmethod
    def _create_source(cls):
        return cls.env['fake.source.static'].create({
            'fake_param': 'some_condition',
            'chunk_size': 5,
        })

    @classmethod
    def _create_consumer(cls):
        return cls.env['fake.source.consumer'].create({
            'name': 'Foo',
            'version': '1.0',
        })

    def test_source_basic(self):
        source = self.source
        self.assertEqual(source.name, 'static')
        self.assertItemsEqual(
            source._config_summary_fields, ['chunk_size', 'fake_param'])

    def test_source_get_lines(self):
        source = self.source
        lines = list(source.get_lines())
        # 20 records, chunk size 5
        self.assertEqual(len(lines), 4)
        # custom sorting: reversed
        self.assertEqual(lines[0][0]['id'], 20)

    def test_source_summary_data(self):
        source = self.source
        data = source._config_summary_data()
        self.assertEqual(data['source'], source)
        self.assertEqual(
            sorted(data['summary_fields']),
            sorted(['chunk_size', 'fake_param']))
        self.assertIn('chunk_size', data['fields_info'])
        self.assertIn('fake_param', data['fields_info'])

    @mock.patch(SOURCE_MODEL + '._selection_source_ref_id')
    def test_consumer_basic(self, _selection_source_ref_id):
        # enable our fake source
        _selection_source_ref_id.return_value = [(self.source._name, 'Fake')]
        consumer = self.consumer
        self.assertFalse(consumer.get_source())
        consumer.update({
            'source_id': self.source.id,
            'source_model': self.source._name
        })
        self.assertEqual(consumer.get_source(), self.source)
