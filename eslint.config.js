/**
 * @fileoverview ESLint config following the Google JavaScript Style Guide.
 * Prettier owns formatting; ESLint checks the rest.
 */

import js from '@eslint/js';
import jsdoc from 'eslint-plugin-jsdoc';
import globals from 'globals';

export default [
  {
    ignores: [
      '.venv/',
      'node_modules/',
      'staticfiles/',
      'htmlcov/',
      'fixtures/',
      'private/',
      // Third-party files, pinned in static/cues/vendor/VERSIONS.
      '**/static/**/vendor/',
    ],
  },
  js.configs.recommended,
  jsdoc.configs['flat/recommended-error'],
  {
    // Google style: Closure type syntax (!Object, ?string, @return) and
    // @fileoverview for the file comment.
    settings: {
      jsdoc: {mode: 'closure', tagNamePreference: {file: 'fileoverview'}},
    },
  },
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: 'module',
      globals: globals.browser,
    },
    rules: {
      // Google style: declarations, equality and control flow.
      'no-var': 'error',
      'prefer-const': 'error',
      'prefer-rest-params': 'error',
      'prefer-spread': 'error',
      eqeqeq: ['error', 'always'],
      curly: ['error', 'all'],
      'new-cap': 'error',
      camelcase: ['error', {properties: 'never'}],
      'guard-for-in': 'error',
      'no-throw-literal': 'error',
      'prefer-promise-reject-errors': 'error',
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      // Google style lets obvious parameters go undescribed.
      'jsdoc/require-param-description': 'off',
      'jsdoc/require-returns-description': 'off',
      // The plugin prefers TypeScript's `object`; Google style writes the
      // Closure type `Object`.
      'jsdoc/check-types': 'off',
      // JSDoc on exported functions and classes.
      'jsdoc/require-jsdoc': [
        'error',
        {
          publicOnly: true,
          require: {FunctionDeclaration: true, ClassDeclaration: true},
        },
      ],
    },
  },
  {
    files: ['eslint.config.js'],
    languageOptions: {globals: globals.node},
  },
];
