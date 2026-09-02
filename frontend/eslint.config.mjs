import nextConfig from "eslint-config-next"

const config = [
  ...nextConfig,
  {
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.object.name='console'][callee.property.name='error'] > Identifier.arguments",
          message:
            "Pass console.error() a derived value (e.g. errorCode(err)) instead of the raw error object.",
        },
      ],
    },
  },
]

export default config
