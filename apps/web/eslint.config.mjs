import parser from '@typescript-eslint/parser';

const effectNames = new Set(['useEffect', 'useLayoutEffect', 'useInsertionEffect']);
const effectOrder = {
  meta: {
    type: 'layout',
    schema: [],
    messages: {
      declarationAfterEffect: 'Declare variables and handlers before effect-only hooks.',
    },
  },
  create: (context) => ({
    BlockStatement: (node) => {
      let hasEffect = false;
      for (const statement of node.body) {
        const expression = statement.type === 'ExpressionStatement' ? statement.expression : null;
        if (expression?.type === 'CallExpression' && effectNames.has(expression.callee.name)) {
          hasEffect = true;
        } else if (
          hasEffect &&
          ['VariableDeclaration', 'FunctionDeclaration'].includes(statement.type)
        ) {
          context.report({ node: statement, messageId: 'declarationAfterEffect' });
        }
      }
    },
  }),
};

export default [
  { ignores: ['node_modules/**', 'dist/**', 'coverage/**'] },
  {
    files: ['**/*.{ts,tsx,js,mjs}'],
    languageOptions: { parser, parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { frontend: { rules: { 'effect-order': effectOrder } } },
    rules: {
      curly: ['error', 'all'],
      'func-style': ['error', 'expression', { allowArrowFunctions: true }],
      'prefer-arrow-callback': 'error',
      'frontend/effect-order': 'error',
    },
  },
];
