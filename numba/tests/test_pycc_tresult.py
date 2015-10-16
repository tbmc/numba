from __future__ import print_function

import contextlib
import imp
import os
import subprocess
import sys
from ctypes import *

import numpy as np

from numba import unittest_support as unittest
from numba.pycc import find_shared_ending, find_pyext_ending, main
from numba.pycc.decorators import clear_export_registry
from .support import TestCase

from numba.tests.support import static_temp_directory

base_path = os.path.dirname(os.path.abspath(__file__))


def unset_macosx_deployment_target():
    """Unset MACOSX_DEPLOYMENT_TARGET because we are not building portable
    libraries
    """
    macosx_target = os.environ.get('MACOSX_DEPLOYMENT_TARGET', None)
    if macosx_target is not None:
        del os.environ['MACOSX_DEPLOYMENT_TARGET']


class BasePYCCTest(TestCase):

    def setUp(self):
        self.tmpdir = static_temp_directory('test_pycc')

    def tearDown(self):
        # Since we're executing the module-under-test several times
        # from the same process, we must clear the exports registry
        # between invocations.
        clear_export_registry()

    @contextlib.contextmanager
    def check_c_ext(self, extdir, name):
        sys.path.append(extdir)
        try:
            lib = __import__(name)
            yield lib
        finally:
            sys.path.remove(extdir)
            sys.modules.pop(name, None)


class TestLegacyAPI(BasePYCCTest):

    def test_pycc_ctypes_lib(self):
        """
        Test creating a C shared library object using pycc.
        """
        unset_macosx_deployment_target()

        source = os.path.join(base_path, 'compile_with_pycc.py')
        cdll_modulename = 'test_dll_legacy' + find_shared_ending()
        cdll_path = os.path.join(self.tmpdir, cdll_modulename)
        if os.path.exists(cdll_path):
            os.unlink(cdll_path)

        main(args=['--debug', '-o', cdll_path, source])
        lib = CDLL(cdll_path)
        lib.mult.argtypes = [POINTER(c_double), c_void_p, c_void_p,
                             c_double, c_double]
        lib.mult.restype = c_int

        lib.multf.argtypes = [POINTER(c_float), c_void_p, c_void_p,
                              c_float, c_float]
        lib.multf.restype = c_int

        res = c_double()
        lib.mult(byref(res), None, None, 123, 321)
        self.assertEqual(res.value, 123 * 321)

        res = c_float()
        lib.multf(byref(res), None, None, 987, 321)
        self.assertEqual(res.value, 987 * 321)

    def test_pycc_pymodule(self):
        """
        Test creating a CPython extension module using pycc.
        """
        self.skipTest("lack of environment can make the extension crash")
        unset_macosx_deployment_target()

        source = os.path.join(base_path, 'compile_with_pycc.py')
        modulename = 'test_pyext_legacy'
        out_modulename = os.path.join(self.tmpdir,
                                      modulename + find_pyext_ending())
        if os.path.exists(out_modulename):
            os.unlink(out_modulename)

        main(args=['--debug', '--python', '-o', out_modulename, source])

        with self.check_c_ext(self.tmpdir, modulename) as lib:
            res = lib.multi(123, 321)
            self.assertPreciseEqual(res, 123 * 321)
            res = lib.multf(987, 321)
            self.assertPreciseEqual(res, 987.0 * 321.0)

    def test_pycc_bitcode(self):
        """
        Test creating a LLVM bitcode file using pycc.
        """
        unset_macosx_deployment_target()

        modulename = os.path.join(base_path, 'compile_with_pycc')
        bitcode_modulename = os.path.join(self.tmpdir, 'test_bitcode_legacy.bc')
        if os.path.exists(bitcode_modulename):
            os.unlink(bitcode_modulename)

        main(args=['--debug', '--llvm', '-o', bitcode_modulename,
                   modulename + '.py'])

        # Sanity check bitcode file contents
        with open(bitcode_modulename, "rb") as f:
            bc = f.read()

        bitcode_wrapper_magic = b'\xde\xc0\x17\x0b'
        bitcode_magic = b'BC\xc0\xde'
        self.assertTrue(bc.startswith((bitcode_magic, bitcode_wrapper_magic)), bc)


class TestCC(BasePYCCTest):

    def setUp(self):
        super(TestCC, self).setUp()
        from . import compile_with_pycc
        self._test_module = compile_with_pycc
        imp.reload(self._test_module)

    @contextlib.contextmanager
    def check_cc_compiled(self, cc):
        #cc.debug = True
        cc.output_dir = self.tmpdir
        cc.compile()

        with self.check_c_ext(self.tmpdir, cc.name) as lib:
            yield lib

    def check_cc_compiled_in_subprocess(self, lib, code):
        prolog = """if 1:
            import sys
            sys.path.insert(0, %(path)r)
            import %(name)s as lib
            """ % {'name': lib.__name__,
                   'path': os.path.dirname(lib.__file__)}
        code = prolog.strip(' ') + code
        subprocess.check_call([sys.executable, '-c', code])

    def test_cc_properties(self):
        cc = self._test_module.cc
        self.assertEqual(cc.name, 'pycc_test_simple')

        # Inferred output directory
        d = self._test_module.cc.output_dir
        self.assertTrue(os.path.isdir(d), d)

        # Inferred output filename
        f = self._test_module.cc.output_file
        self.assertFalse(os.path.exists(f), f)
        self.assertTrue(os.path.basename(f).startswith('pycc_test_simple.'), f)
        if sys.platform == 'linux':
            self.assertTrue(f.endswith('.so'), f)
            if sys.version_info >= (3,):
                self.assertIn('.cpython', f)

    def test_compile(self):
        with self.check_cc_compiled(self._test_module.cc) as lib:
            res = lib.multi(123, 321)
            self.assertPreciseEqual(res, 123 * 321)
            res = lib.multf(987, 321)
            self.assertPreciseEqual(res, 987.0 * 321.0)
            res = lib.square(5)
            self.assertPreciseEqual(res, 25)

    def test_compile_helperlib(self):
        with self.check_cc_compiled(self._test_module.cc_helperlib) as lib:
            res = lib.power(2, 7)
            self.assertPreciseEqual(res, 128)
            for val in (-1, -1 + 0j, np.complex128(-1)):
                res = lib.sqrt(val)
                self.assertPreciseEqual(res, 1j)
            res = lib.random(42)
            expected = np.random.RandomState(42).random_sample()
            self.assertPreciseEqual(res, expected)
            res = lib.size(np.float64([0] * 3))
            self.assertPreciseEqual(res, 3)

            code = """if 1:
                from numpy.testing import assert_equal, assert_allclose
                res = lib.power(2, 7)
                assert res == 128
                res = lib.random(42)
                assert_allclose(res, %(expected)s)
                """ % {'expected': expected}
            self.check_cc_compiled_in_subprocess(lib, code)

    def test_compile_nrt(self):
        with self.check_cc_compiled(self._test_module.cc_nrt) as lib:
            # Sanity check
            self.assertPreciseEqual(lib.zero_scalar(1), 0.0)
            res = lib.zeros(3)
            self.assertEqual(list(res), [0, 0, 0])

            code = """if 1:
                res = lib.zero_scalar(1)
                assert res == 0.0
                res = lib.zeros(3)
                assert list(res) == [0, 0, 0]
                """
            self.check_cc_compiled_in_subprocess(lib, code)


if __name__ == "__main__":
    unittest.main()
